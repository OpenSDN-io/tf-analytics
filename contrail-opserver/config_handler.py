#
# Copyright (c) 2016 Juniper Networks, Inc. All rights reserved.
#


import gevent
import json
import traceback

from cfgm_common.vnc_amqp import VncAmqpHandle
try:
    # due to different behaviour in 'UT'
    from cfgm_common.vnc_object_db import VncObjectDBClient
except ImportError:
    pass
from .analytics_logger import AnalyticsLogger


class ConfigHandler(object):
    def __init__(self, sandesh, service_id, rabbitmq_cfg, cassandra_cfg,
                 db_cls, reaction_map, host_ip, zk_servers):
        self._sandesh = sandesh
        self._logger = AnalyticsLogger(self._sandesh)
        self._service_id = service_id
        self._rabbitmq_cfg = rabbitmq_cfg
        self._cassandra_cfg = cassandra_cfg
        self._db_cls = db_cls
        self._reaction_map = reaction_map
        self._vnc_amqp = None
        self._vnc_db = None
        self.host_ip = host_ip
        self.zk_servers = zk_servers
    # end __init__

    # Public methods

    def start(self):
        # Connect to rabbitmq for config update notifications
        while True:
            try:
                self._vnc_amqp = VncAmqpHandle(self._sandesh, self._logger,
                    self._db_cls, self._reaction_map, self._service_id,
                    self._rabbitmq_cfg, self.host_ip)
                self._vnc_amqp.establish()
            except Exception as e:
                template = 'Exception {0} connecting to Rabbitmq. Arguments:\n{1!r}'
                msg = template.format(type(e).__name__, e.args)
                self._logger.error('%s: %s' % (msg, traceback.format_exc()))
                gevent.sleep(2)
            else:
                break
        cassandra_credential = {
            'username': self._cassandra_cfg['user'],
            'password': self._cassandra_cfg['password']
        }
        if not all(cassandra_credential.values()):
            cassandra_credential = None
        try:
            self._vnc_db = VncObjectDBClient(self._cassandra_cfg['servers'],
                self._cassandra_cfg['cluster_id'], logger=self._logger.log,
                credential=cassandra_credential,
                ssl_enabled=self._cassandra_cfg['use_ssl'],
                ca_certs=self._cassandra_cfg['ca_certs'],
                cassandra_driver=self._cassandra_cfg['cassandra_driver'],
                zk_servers=self.zk_servers)
        except Exception as e:
            template = 'Exception {0} connecting to Config DB. Arguments:\n{1!r}'
            msg = template.format(type(e).__name__, e.args)
            self._logger.error('%s: %s' % (msg, traceback.format_exc()))
            exit(2)
        self._db_cls.init(self, self._logger, self._vnc_db)
        self._sync_config_db()
    # end start

    def stop(self):
        self._vnc_amqp.close()
        self._vnc_db = None
        self._db_cls.clear()
    # end stop

    def obj_to_dict(self, obj):
        def to_json(obj):
            if hasattr(obj, 'serialize_to_json'):
                return obj.serialize_to_json()
            else:
                return dict((k, v) for k, v in obj.__dict__.items())

        return json.loads(json.dumps(obj, default=to_json))
    # end obj_to_dict

    # Private methods

    def _fqname_to_str(self, fq_name):
        return ':'.join(fq_name)
    # end _fqname_to_str

    def _sync_config_db(self):
        for cls in list(self._db_cls.get_obj_type_map().values()):
            cls.reinit()
        self._handle_config_sync()
        self._vnc_amqp._db_resync_done.set()
    # end _sync_config_db

    # Should be overridden by the derived class
    def _handle_config_sync(self):
        pass
    # end _handle_config_sync


# end class ConfigHandler
