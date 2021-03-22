# -*- coding: utf-8 -*-
#######
# actinia-core - an open source REST API for scalable, distributed, high
# performance processing of geographical data that uses GRASS GIS for
# computational tasks. For details, see https://actinia.mundialis.de/
#
# Copyright (c) 2016-2018 Sören Gebbert and mundialis GmbH & Co. KG
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#######

"""
This module takes care of the status requests that are generated by asynchronous
processes.
"""

import os
import pickle
import re
from flask import g
from flask import jsonify, make_response
from flask_restful_swagger_2 import Resource
from flask_restful_swagger_2 import swagger
from flask_restful import reqparse
from time import sleep
from .common.app import auth
from .common.config import global_config
from .common.redis_interface import enqueue_job
from .common.resources_logger import ResourceLogger
from .common.api_logger import log_api_call
from .common.user import ActiniaUser
from .common.response_models import ProcessingResponseModel, SimpleResponseModel,\
    ProcessingResponseListModel

__license__ = "GPLv3"
__author__ = "Sören Gebbert, Anika Weinmann"
__copyright__ = "Copyright 2016-2021, Sören Gebbert and mundialis GmbH & Co. KG"
__maintainer__ = "mundialis"


class ResourceManagerBase(Resource):
    """Base class for resource management
    """
    decorators = [log_api_call, auth.login_required]

    def __init__(self):

        # Configuration
        Resource.__init__(self)

        kwargs = dict()
        kwargs['host'] = global_config.REDIS_SERVER_URL
        kwargs['port'] = global_config.REDIS_SERVER_PORT
        if global_config.REDIS_SERVER_PW and global_config.REDIS_SERVER_PW is not None:
            kwargs['password'] = global_config.REDIS_SERVER_PW
        self.resource_logger = ResourceLogger(**kwargs)
        del kwargs

        # Store the user id, user group and all credentials of the current user

        self.user = g.user
        self.user_id = g.user.get_id()
        self.user_group = g.user.get_group()
        self.user_role = g.user.get_role()
        self.user_credentials = g.user.get_credentials()

    def check_permissions(self, user_id):
        """Check the access rights of the user that calls this API call

        Permission:
            - guest and user roles can only access resources of the same user id
            - admin role are allowed to access resources of users with the same
              user group, except for superusers
            - superdamins role can access all resources

        Args:
            user_id:

        Returns:
            None if permissions granted, a error response if permissions are
            not fulfilled

        """
        # Superuser are allowed to do everything
        if self.user.has_superadmin_role() is True:
            return None

        # Check permissions for users and guests
        if self.user_role == "guest" or self.user_role == "user":
            if self.user_id != user_id:
                return make_response(jsonify(SimpleResponseModel(
                    status="error",
                    message="You do not have the permission to access this resource. "
                            "Wrong user.")), 401)
        new_user = ActiniaUser(user_id=user_id)

        # Check if the user exists
        if new_user.exists() is False:
            return make_response(jsonify(SimpleResponseModel(
                status="error",
                message="The user <%s> does not exist" % user_id)), 400)

        # Check admin permissions
        if self.user_role == "admin":
            # Resources of superusers are not allowed to be accessed
            if new_user.has_superadmin_role() is True:
                return make_response(jsonify(SimpleResponseModel(
                    status="error",
                    message="You do not have the permission to access this resource. "
                            "Wrong user role.")), 401)
            # Only resources of the same user group are allowed to be accessed
            if new_user.get_group() != self.user_group:
                return make_response(jsonify(SimpleResponseModel(
                    status="error",
                    message="You do not have the permission to access this resource. "
                            "Wrong user group.")), 401)
        return None


class ResourceManager(ResourceManagerBase):
    """
    This class is responsible to answer status requests
    of asynchronous processes (resources) and
    to request the termination of a resource
    """
    def __init__(self):

        # Configuration
        ResourceManagerBase.__init__(self)

    @swagger.doc({
        'tags': ['Resource Management'],
        'description': 'Get the status of a resource. Minimum required user '
                       'role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'resource_id',
                'description': 'The id of the resource',
                'required': True,
                'in': 'path',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'The current state of the resource',
                'schema': ProcessingResponseModel
            },
            '400': {
                'description': 'The error message if the resource does not exists',
                'schema': SimpleResponseModel
            }
        }
    })
    def get(self, user_id, resource_id):
        """Get the status of a resource."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        # the latest iteration should be given
        if resource_id.startswith('resource_id-'):
            _, response_data = self.resource_logger.get_latest_iteration(
                user_id, resource_id)
        else:
            response_data = self.resource_logger.get_all_iteration(
                user_id, 'resource_id-%s' % resource_id)

        if response_data is not None:
            http_code, response_model = pickle.loads(response_data)
            return make_response(jsonify(response_model), http_code)
        else:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Resource does not exist")), 400)

    @swagger.doc({
        'tags': ['Resource Management'],
        'description': 'Updates/Resumes the status of a resource. '
                       'Minimum required user role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'resource_id',
                'description': 'The id of the resource',
                'required': True,
                'in': 'path',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'The current state of the resource',
                'schema': ProcessingResponseModel
            },
            '400': {
                'description': 'The error message if the resource does not exists',
                'schema': SimpleResponseModel
            }
        }
     })
    def put(self, user_id, resource_id):
        """Updates/Resumes the status of a resource."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        # check if in general interim results are saved
        if global_config.SAVE_INTERIM_RESULTS is False:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Saving iterim results is not configured")), 400)

        # check if latest iteration is found
        old_iteration, response_data = self.resource_logger.get_latest_iteration(
            user_id, resource_id)
        if response_data is None:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Resource does not exist")), 400)

        # check if a new iteration is possible (only if status is error or
        # terminated; or status is running but processing time is not changing
        # any more)
        http_code, response_model = pickle.loads(response_data)
        if response_model is None:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Resource has no response model")), 400)
        if response_model['status'] in ['accepted', 'finished']:
            return make_response(jsonify(SimpleResponseModel(
                status="error",
                message=f"Resource is {response_model['status']} PUT not "
                         "possible")), 400)
        elif response_model['status'] in ['running']:
            # check if status is running but processing time is not changing
            # any more
            first_time_delta = response_model['time_delta']
            sleep(5)
            old_iteration2, response_data2 = self.resource_logger.get_latest_iteration(
                user_id, resource_id)
            if response_data2 is None:
                return make_response(jsonify(SimpleResponseModel(
                    status="error", message="Resource does not exist")), 400)
            http_code2, response_model2 = pickle.loads(response_data2)
            if response_model is None:
                return make_response(jsonify(SimpleResponseModel(
                    status="error", message="Resource has no response model")), 400)
            # check time_delta
            if response_model['time_delta'] == response_model2['time_delta']:
                # process is not running any more and can be restarted
                pass
            else:
                return make_response(jsonify(SimpleResponseModel(
                    status="error", message="Resource is running no restart possible")), 400)
        elif response_model['status'] in ['error', 'terminated']:
            pass

        # check if interim results are saved
        user_resource_interim_storage_path = os.path.join(
            global_config.GRASS_RESOURCE_DIR, g.user.get_id(),
            "interim", resource_id)
        interim_folder = os.listdir(user_resource_interim_storage_path)
        pc_step = response_model['progress']['step'] - 1
        if interim_folder[0] != f"step{str(pc_step)}":
            iterim_error = True
        if interim_folder[0] != f"step{str(pc_step)}":
            return make_response(jsonify(SimpleResponseModel(
                status="error",
                message="No interim results saved in previous iteration for "
                       f"step {str(pc_step)}")), 400)

        # start new iteration
        iteration = old_iteration + 1

        # use post_url if iteration > 1
        if old_iteration and old_iteration == 1:
            post_url = response_model['api_info']['request_url']
        elif old_iteration and old_iteration > 1:
            post_url = response_model['api_info']['post_url']
        else:
            post_url = None

        # check the old processing type
        processing_type = post_url.split('/')[-1]
        location = re.findall(r'locations\/(.*?)\/', post_url)[0]
        if processing_type == 'processing_async' and not 'mapsets' in post_url:
            # '/locations/<string:location_name>/processing_async'
            # here are debuggers: ephemeral_processing.py(1563)_create_temporary_grass_environment_and_process_list_for_iteration()
            from .ephemeral_processing import AsyncEphemeralResource, start_job
            processing_resource = AsyncEphemeralResource(
                resource_id, iteration, post_url)
            rdc = processing_resource.preprocess(location_name=location)
        elif processing_type == 'processing_async' and 'mapsets' in post_url:
            # /locations/{location_name}/mapsets/{mapset_name}/processing_async
            # here are debuggers: ephemeral_processing.py(1563)_create_temporary_grass_environment_and_process_list_for_iteration()
            from .persistent_processing import AsyncPersistentResource, start_job
            processing_resource = AsyncPersistentResource(
                resource_id, iteration, post_url)
            mapset = re.findall(r'mapsets\/(.*?)\/', post_url)[0]
            rdc = processing_resource.preprocess(
                location_name=location, mapset_name=mapset)
        elif processing_type == 'processing_async_export':
            # /locations/{location_name}/processing_async_export
            # here are debuggers: ephemeral_processing.py(1563)_create_temporary_grass_environment_and_process_list_for_iteration()
            from .ephemeral_processing_with_export import \
                AsyncEphemeralExportResource, start_job
            processing_resource = AsyncEphemeralExportResource(
                resource_id, iteration, post_url)
            rdc = processing_resource.preprocess(
                location_name=location)
        else:
            # TODO ?
            # /locations/{location_name}/gdi_processing_async_export
            # /locations/{location_name}/mapsets/{mapset_name}/gdi_processing_async
            return make_response(jsonify(SimpleResponseModel(
                status="error",
                message=f"Processing endpoint {post_url} does not support put")), 400)

        ## enqueue job
        if rdc:
            enqueue_job(processing_resource.job_timeout, start_job, rdc)
        html_code, response_model = pickle.loads(processing_resource.response_data)
        return make_response(jsonify(response_model), html_code)

    @swagger.doc({
        'tags': ['Resource Management'],
        'description': 'Request the termination of a resource. '
                       'Minimum required user role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'resource_id',
                'description': 'The id of the resource',
                'required': True,
                'in': 'path',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'Returned if termination request of the resource '
                               'was successfully committed. '
                               'Be aware that this does not mean, that the '
                               'resource was successfully terminated.',
                'schema': SimpleResponseModel
            },
            '400': {
                'description': 'The error message why resource storage '
                               'information gathering did not succeeded',
                'schema': SimpleResponseModel
            }
        }
    })
    def delete(self, user_id, resource_id):
        """Request the termination of a resource."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        if not resource_id.startswith('resource_id-'):
            resource_id = 'resource_id-%s' % resource_id

        _, doc = self.resource_logger.get_latest_iteration(user_id, resource_id)

        if doc is None:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Resource does not exist")), 400)

        self.resource_logger.commit_termination(user_id, resource_id)

        return make_response(jsonify(SimpleResponseModel(
            status="accepted", message="Termination request committed")), 200)


# Create a g.list/g.remove pattern parser
resource_parser = reqparse.RequestParser()
resource_parser.add_argument(
    'num', type=int, help='The maximum number of jobs that should be listed',
    location='args')
resource_parser.add_argument(
    'type', type=str,
    help='The type of the jobs that should be shown: '
         'all, running, error, terminated, finished',
    location='args')


class ResourcesManager(ResourceManagerBase):
    """Management of multiple resources

    TODO: This methods must be secured by checking the user id. Only admins
    can terminate and list resources from other users.

    """

    def __init__(self):

        # Configuration
        ResourceManagerBase.__init__(self)

    def _get_resource_list(self, user_id, type_="all"):
        """Get a list of resources that have been generated by the calling user
        """
        parsed_list = []
        resource_list = self.resource_logger.get_user_resources(user_id)

        for entry in resource_list:
            if type_.lower() == "all":
                parsed_list.append(entry)
            elif type_.lower() == entry["status"]:
                parsed_list.append(entry)

        return parsed_list

    @swagger.doc({
        'tags': ['Resource Management'],
        'description': 'Get a list of resources that have been generated by the '
                       'specified user. Minimum required user role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'num',
                'description': 'The maximum number of jobs that should be returned',
                'required': False,
                'in': 'query',
                'type': 'integer'
            },
            {
                'name': 'type',
                'description': 'The type of job that should be returned: '
                               'accepted, running, error, terminated, finished',
                'required': False,
                'in': 'query',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'Returned a list of resources that have been '
                               'generated by the specified user.',
                'schema': ProcessingResponseListModel
            },
            '401': {
                'description': 'The error message why resource gathering did '
                               'not succeeded',
                'schema': SimpleResponseModel
            }
        }
    })
    def get(self, user_id):
        """Get a list of resources that have been generated by the specified user."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        args = resource_parser.parse_args()
        num = None
        if "num" in args and args["num"]:
            num = args["num"]
        type_ = "all"
        if "type" in args and args["type"]:
            type_ = args["type"]

        resource_list = self._get_resource_list(user_id, type_=type_)

        if num is not None:
            response_list = resource_list[0:num]
        else:
            response_list = resource_list

        return make_response(jsonify(ProcessingResponseListModel(
            resource_list=response_list)), 200)

    @swagger.doc({
        'tags': ['Resource Management'],
        'description': 'Terminate all accepted and running resources of the specified '
                       'user. Minimum required user role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'Termination requests have been successfully '
                               'committed. Be aware that does not mean, that '
                               'the resources have been successfully terminated.',
                'schema': SimpleResponseModel
            },
            '401': {
                'description': 'The error message why the resource termination '
                               'did not succeeded',
                'schema': SimpleResponseModel
            }
        }
    })
    def delete(self, user_id):
        """Terminate all accepted and running resources of the specified user."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        resource_list = self._get_resource_list(user_id)

        termination_requests = 0
        for entry in resource_list:
            if "status" in entry:
                if entry["status"] in ["accepted", "running"]:
                    self.resource_logger.commit_termination(
                        user_id, entry["resource_id"])
                    termination_requests += 1

        return make_response(jsonify(SimpleResponseModel(
            status="finished",
            message="Successfully send %i termination requests"
                    % termination_requests)), 200)


class ResourceIterationManager(ResourceManagerBase):
    """
    This class is responsible to answer status requests
    of asynchronous processes (resources) and
    to request the termination of a resource with iterations
    """
    def __init__(self):

        # Configuration
        ResourceManagerBase.__init__(self)

    @swagger.doc({
        'tags': ['Resource Iteration Management'],
        'description': 'Get the status of a resource with the iterations. '
                       'Minimum required user role: user.',
        'parameters': [
            {
                'name': 'user_id',
                'description': 'The unique user name/id',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'resource_id',
                'description': 'The id of the resource',
                'required': True,
                'in': 'path',
                'type': 'string'
            },
            {
                'name': 'iteration',
                'description': 'The id of the resource',
                'required': True,
                'in': 'path',
                'type': 'string'
            }
        ],
        'responses': {
            '200': {
                'description': 'The current state of the resource',
                'schema': ProcessingResponseModel
            },
            '400': {
                'description': 'The error message if the resource does not exists',
                'schema': SimpleResponseModel
            }
        }
     })
    def get(self, user_id, resource_id, iteration):
        """Get the status of a resource of a given iteration."""

        ret = self.check_permissions(user_id=user_id)
        if ret:
            return ret

        if not resource_id.startswith('resource_id-'):
            resource_id = 'resource_id-%s' % resource_id

        if iteration == 'latest':
            iteration, response_data = self.resource_logger.get_latest_iteration(
                user_id, resource_id,)
        else:
            response_data = self.resource_logger.get(
                user_id, resource_id, int(iteration))

        if response_data is not None:
            http_code, tmp_response_model = pickle.loads(response_data)
            response_model = {str(iteration): tmp_response_model}
            return make_response(jsonify(response_model), http_code)
        else:
            return make_response(jsonify(SimpleResponseModel(
                status="error", message="Resource does not exist")), 400)
