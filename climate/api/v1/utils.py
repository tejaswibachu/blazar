# Copyright (c) 2013 Mirantis Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import traceback

import flask
from oslo_log import log as logging
import oslo_messaging as messaging
from oslo_serialization import jsonutils
from werkzeug import datastructures

from climate.api import context
from climate.db import exceptions as db_exceptions
from climate import exceptions as ex
from climate.i18n import _
from climate.manager import exceptions as manager_exceptions

LOG = logging.getLogger(__name__)


class Rest(flask.Blueprint):
    """REST helper class."""

    def get(self, rule, status_code=200):
        return self._mroute('GET', rule, status_code)

    def post(self, rule, status_code=202):
        return self._mroute('POST', rule, status_code)

    def put(self, rule, status_code=202):
        return self._mroute('PUT', rule, status_code)

    def delete(self, rule, status_code=204):
        return self._mroute('DELETE', rule, status_code)

    def _mroute(self, methods, rule, status_code=None, **kw):
        """Route helper method."""
        if type(methods) is str:
            methods = [methods]
        return self.route(rule, methods=methods, status_code=status_code, **kw)

    def route(self, rule, **options):
        """Routes REST method and its params to the actual request."""
        status = options.pop('status_code', None)
        file_upload = options.pop('file_upload', False)

        def decorator(func):
            endpoint = options.pop('endpoint', func.__name__)

            def handler(**kwargs):
                LOG.debug("Rest.route.decorator.handler, kwargs=%s", kwargs)

                _init_resp_type(file_upload)

                # update status code
                if status:
                    flask.request.status_code = status

                if flask.request.method in ['POST', 'PUT']:
                    kwargs['data'] = request_data()

                with context.ctx_from_headers(flask.request.headers):
                    try:
                        return func(**kwargs)
                    except ex.ClimateException as e:
                        return bad_request(e)
                    except messaging.RemoteError as e:
                        # Get the exception from manager and common exceptions
                        cls = getattr(manager_exceptions, e.exc_type,
                                      getattr(ex, e.exc_type, None))
                        if cls is not None:
                            return render_error_message(cls.code, e.value,
                                                        cls.code)
                        else:
                            # Get the exception from db exceptions and hide
                            # the message because could contain table/column
                            # information
                            cls = getattr(db_exceptions, e.exc_type, None)
                            if cls is not None:
                                return render_error_message(
                                    cls.code,
                                    '{0}: A database error occurred'.format(
                                        cls.__name__),
                                    cls.code)
                            else:
                                # We obfuscate all Exceptions
                                # but Climate ones for
                                # security reasons
                                err = 'Internal Server Error'
                                return internal_error(500, err, e)
                    except Exception as e:
                        return internal_error(500, 'Internal Server Error', e)

            self.add_url_rule(rule, endpoint, handler, **options)
            self.add_url_rule(rule + '.json', endpoint, handler, **options)

            return func

        return decorator


RT_JSON = datastructures.MIMEAccept([("application/json", 1)])


def _init_resp_type(file_upload):
    """Extracts response content type."""

    # get content type from Accept header
    resp_type = flask.request.accept_mimetypes

    # url /foo.json
    if flask.request.path.endswith('.json'):
        resp_type = RT_JSON

    flask.request.resp_type = resp_type

    # set file upload flag
    flask.request.file_upload = file_upload


def render(result=None, response_type=None, status=None, **kwargs):
    """Render response to return."""
    if not result:
        result = {}
    if type(result) is dict:
        result.update(kwargs)
    elif kwargs:
        # can't merge kwargs into the non-dict res
        abort_and_log(500,
                      _("Non-dict and non-empty kwargs passed to render."))
        return

    status_code = getattr(flask.request, 'status_code', None)
    if status:
        status_code = status
    if not status_code:
        status_code = 200

    if not response_type:
        response_type = getattr(flask.request, 'resp_type', RT_JSON)

    serializer = None
    if "application/json" in response_type:
        response_type = RT_JSON
        serializer = jsonutils
    else:
        abort_and_log(400,
                      _("Content type '%s' isn't supported") % response_type)
        return

    body = serializer.dumps(result)

    response_type = str(response_type)

    return flask.Response(response=body, status=status_code,
                          mimetype=response_type)


def request_data():
    """Method called to process POST and PUT REST methods."""
    if hasattr(flask.request, 'parsed_data'):
        return flask.request.parsed_data

    if not flask.request.content_length > 0:
        LOG.debug("Empty body provided in request")
        return dict()

    if flask.request.file_upload:
        return flask.request.data

    deserializer = None
    content_type = flask.request.mimetype
    if not content_type or content_type in RT_JSON:
        deserializer = jsonutils
    else:
        abort_and_log(400,
                      _("Content type '%s' isn't supported") % content_type)
        return

    # parsed request data to avoid unwanted re-parsings
    parsed_data = deserializer.loads(flask.request.data)
    flask.request.parsed_data = parsed_data

    return flask.request.parsed_data


def get_request_args():
    return flask.request.args


def abort_and_log(status_code, descr, exc=None):
    """Process occurred errors."""
    LOG.error(_("Request aborted with status code %(code)s and "
                "message '%(msg)s'"), {'code': status_code, 'msg': descr})

    if exc is not None:
        LOG.error(traceback.format_exc())

    flask.abort(status_code, description=descr)


def render_error_message(error_code, error_message, error_name):
    """Render nice error message."""
    message = {
        "error_code": error_code,
        "error_message": error_message,
        "error_name": error_name
    }

    resp = render(message)
    resp.status_code = error_code

    return resp


def internal_error(status_code, descr, exc=None):
    """Called if internal error occurred."""
    LOG.error(_("Request aborted with status code %(code)s "
                "and message '%(msg)s'"), {'code': status_code, 'msg': descr})

    if exc is not None:
        LOG.error(traceback.format_exc())

    error_code = "INTERNAL_SERVER_ERROR"
    if status_code == 501:
        error_code = "NOT_IMPLEMENTED_ERROR"

    return render_error_message(status_code, descr, error_code)


def bad_request(error):
    """Called if Climate exception occurred."""
    if not error.code:
        error.code = 400

    LOG.debug("Validation Error occurred: error_code=%(code)s, "
              "error_message=%(msg)s, error_name=%(name)s",
              {'code': error.code, 'msg': error.message, 'name': error.code})

    return render_error_message(error.code, error.message, error.code)


def not_found(error):
    """Called if object was not found."""
    if not error.code:
        error.code = 404

    LOG.debug("Not Found exception occurred: error_code=%(code)s, "
              "error_message=%(msg)s, error_name=%(name)s",
              {'code': error.code, 'msg': error.message, 'name': error.code})

    return render_error_message(error.code, error.message, error.code)
