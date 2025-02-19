#
# Copyright (c) 2015 Juniper Networks, Inc. All rights reserved.
#
import socket
from pysandesh.sandesh_base import *
from pysandesh.connection_info import ConnectionState
from .sandesh.nodeinfo.ttypes import NodeStatusUVE, NodeStatus
from .sandesh.link.ttypes import LinkEntry, PRouterLinkEntry, PRouterLinkUVE
from sandesh_common.vns.ttypes import Module
from sandesh_common.vns.constants import ModuleNames,\
     Module2NodeType, NodeTypeNames

class LinkUve(object):
    def __init__(self, conf):
        self._conf = conf
        module = Module.CONTRAIL_TOPOLOGY
        self._moduleid = ModuleNames[module]
        node_type = Module2NodeType[module]
        self._node_type_name = NodeTypeNames[node_type]
        if 'host_ip' in self._conf._args:
            host_ip = self._conf._args.host_ip
        else:
            host_ip = socket.gethostbyname(socket.getfqdn())
        self._hostname = socket.getfqdn(host_ip)
        self.table = "ObjectAnalyticsSNMPInfo"
        self._instance_id = '0'
        sandesh_global.init_generator(self._moduleid, self._hostname,
                                      self._node_type_name, self._instance_id,
                                      self._conf.random_collectors,
                                      self._node_type_name,
                                      self._conf.http_port(),
                                      ['tf_topology.sandesh'],
                                      config=self._conf.sandesh_config())
        sandesh_global.set_logging_params(
            enable_local_log=self._conf.log_local(),
            category=self._conf.log_category(),
            level=self._conf.log_level(),
            file=self._conf.log_file(),
            enable_syslog=self._conf.use_syslog(),
            syslog_facility=self._conf.syslog_facility())
        ConnectionState.init(sandesh_global, self._hostname, self._moduleid,
            self._instance_id,
            staticmethod(ConnectionState.get_conn_state_cb),
            NodeStatusUVE, NodeStatus, self.table)
        self._logger = sandesh_global.logger()
        # end __init__

    def sandesh_instance(self):
        return sandesh_global
    # end sandesh_instance

    def logger(self):
        return self._logger
    # end logger

    def stop(self):
        sandesh_global.uninit()
    # end stop

    def send(self, data):
        for prouter in data:
            lt = [LinkEntry(**x) for x in data[prouter]]
            uve = PRouterLinkUVE(data=PRouterLinkEntry(name=prouter,
                        link_table=lt))
            uve.send()

    def delete(self, name):
         PRouterLinkUVE(data=PRouterLinkEntry(name=name, deleted=True)).send()

    def sandesh_reconfig_collectors(self, collectors):
        sandesh_global.reconfig_collectors(collectors)
