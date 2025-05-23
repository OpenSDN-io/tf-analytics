#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

#
# UVEServer
#
# Operational State Server for UVEs
#

import gevent
import json
import copy
import xmltodict
import socket
from .opserver_util import OpServerUtils
import re
from pysandesh.util import UTCTimestampUsec
from pysandesh.connection_info import ConnectionState
from .sandesh.viz.constants import UVE_MAP
from pysandesh.gen_py.process_info.ttypes import ConnectionType,\
     ConnectionStatus
import traceback
from collections import namedtuple
from .strict_redis_wrapper import StrictRedisWrapper
from .opserver_util import convert_to_string

more_than_100k = 0 

RedisInfo = namedtuple("RedisInfo",["ip","port","pid"])

RedisInstKey = namedtuple("RedisInstKey",["ip","port"])
class RedisInst(object):
    def __init__(self):
        self.redis_handle = None
        self.collector_pid = None
        self.deleted = False

class UVEServer(object):

    def __init__(self, redis_uve_list, logger,
            redis_password=None, redis_ssl_params=None, \
            uvedbcache=None, usecache=False, freq=5):
        self._logger = logger
        self._redis = None
        self._uvedbcache = uvedbcache
        self._usecache = usecache
        self._redis_cfg_info = []
        self._redis_password = redis_password
        self._redis_ssl_params = redis_ssl_params;
        self._uve_reverse_map = {}
        self._freq = freq
        self._active_collectors = []

        for h,m in UVE_MAP.items():
            self._uve_reverse_map[m] = h

        # Fill in redis/collector instances
        self._redis_uve_map = {}
        for new_elem in redis_uve_list:
            test_elem = RedisInstKey(ip=new_elem[0], port=new_elem[1])
            self._redis_cfg_info.append(test_elem)
            self._redis_uve_map[test_elem] = RedisInst()
            ConnectionState.update(ConnectionType.REDIS_UVE,\
                test_elem.ip+":"+str(test_elem.port), ConnectionStatus.INIT,
                [test_elem.ip+":"+str(test_elem.port)],"Redis Instance initialized")
    #end __init__

    def collectors_change_cb(self, children):
        self._active_collectors = children
        redis_uve_list = []
        for redis_cfg in self._redis_cfg_info:
            redis_fqdn = socket.getfqdn(redis_cfg[0])
            if redis_fqdn in self._active_collectors:
                redis_elem = (socket.gethostbyname(redis_cfg[0]),
                        redis_cfg[1])
                redis_uve_list.append(redis_elem)
        self.update_redis_uve_list(redis_uve_list)

    def fill_redis_uve_info(self, redis_uve_info):
        try:
            for rkey,rinst in self._redis_uve_map.items():
                rinst.redis_handle.ping()
        except:
            redis_uve_info.status = 'DisConnected'
        else:
            redis_uve_info.status = 'Connected'
    #end fill_redis_uve_info

    def redis_instances(self):
        ril = []
        for rkey,rinst in self._redis_uve_map.items():
            # A redis instance is only valid if we also know the collector pid
            if rinst.redis_handle is not None and \
                    rinst.collector_pid is not None:
                cpid = convert_to_string(rinst.collector_pid).split(':')[3]
                ril.append(RedisInfo(ip=rkey.ip, port=rkey.port, pid=cpid))
        return set(ril)

    def update_redis_uve_list(self, redis_uve_list):
        newlist = set(redis_uve_list)
        # if some redis instances are gone, remove them from our map
        for test_elem in list(self._redis_uve_map.keys()):
            r_ip = test_elem[0]
            r_port = test_elem[1]
            redis_inst = (r_ip, int(r_port))
            if redis_inst not in newlist:
                self._redis_uve_map[test_elem].deleted = True
            else:
                self._redis_uve_map[test_elem].deleted = False

        # new redis instances need to be inserted into the map
        for new_elem in newlist:
            new_redis = RedisInstKey(ip=new_elem[0], port=new_elem[1])
            if new_redis not in self._redis_uve_map:
                self._redis_uve_map[new_redis] = RedisInst()
                ConnectionState.update(conn_type = ConnectionType.REDIS_UVE,\
                        name = new_elem[0]+":"+str(new_elem[1]), status = \
                        ConnectionStatus.INIT, server_addrs = \
                        [new_elem[0]+":"+str(new_elem[1])],
                        message = "Insert New Redis Instance")
    # end update_redis_uve_list

    def run(self):
        exitrun = False
        while not exitrun:
            for rkey in list(self._redis_uve_map.keys()):
                rinst = self._redis_uve_map[rkey]
                old_pid = rinst.collector_pid
                try:
                    # check if it is marked as deleted during sighup handling
                    if rinst.deleted == True:
                        r_ip = rkey[0]
                        r_port = rkey[1]
                        del self._redis_uve_map[rkey]
                        ConnectionState.delete(ConnectionType.REDIS_UVE,\
                            r_ip+":"+str(r_port))
                        continue

                    if rinst.redis_handle is None:
                        rinst.redis_handle = StrictRedisWrapper(
                            host=rkey.ip, port=rkey.port,
                            password=self._redis_password, db=1,
                            socket_timeout=30, **self._redis_ssl_params)
                        rinst.collector_pid = None

                    # check for known collector pid string
                    # if there's a mismatch, we must read it again
                    if rinst.collector_pid is not None:
                        if not rinst.redis_handle.sismember("NGENERATORS", rinst.collector_pid):
                            rinst.collector_pid = None

                    # read the collector pid string
                    if rinst.collector_pid is None:
                        for gen in rinst.redis_handle.smembers("NGENERATORS"):
                            module = convert_to_string(gen).split(':')[2]
                            if module == "contrail-collector":
                                rinst.collector_pid = gen
                except gevent.GreenletExit:
                    self._logger.error('UVEServer Exiting on gevent-kill')
                    exitrun = True
                    break
                except Exception as e:
                    self._logger.debug("redis/collector healthcheck failed %s for %s" \
                                   % (str(e), str(rkey)))
                    rinst.redis_handle = None
                    rinst.collector_pid = None
                finally:
                    # Update redis/collector health
                    '''
                    when rinst.redis_handle is none, redis down
                    when rkey.ip not in collectors, collector down
                    if redis and collector are up or down, state should be up
                    if redis is up, state should be up
                    if redis is down but collector is up, the state shoue be down
                    '''
                    if rkey in list(self._redis_uve_map.keys()):
                        if rinst.redis_handle is None:
                            if rkey.ip != '127.0.0.1':
                                rkey_fqdn = socket.getfqdn(rkey.ip)
                            else:
                                rkey_fqdn = socket.getfqdn()
                            if (self._active_collectors is not None and
                                    rkey_fqdn not in self._active_collectors):
                                ConnectionState.update(ConnectionType.REDIS_UVE,\
                                    rkey.ip + ":" + str(rkey.port), ConnectionStatus.UP,
                                    [rkey.ip+":"+str(rkey.port)],"Redis Instance is Up")
                            else:
                                ConnectionState.update(ConnectionType.REDIS_UVE,\
                                    rkey.ip + ":" + str(rkey.port), ConnectionStatus.DOWN,
                                    [rkey.ip+":"+str(rkey.port)],"Redis Instance is Down")
                        else:
                            ConnectionState.update(ConnectionType.REDIS_UVE,\
                                rkey.ip + ":" + str(rkey.port), ConnectionStatus.UP,
                                [rkey.ip+":"+str(rkey.port)])
            if not exitrun:
                gevent.sleep(self._freq)

    @staticmethod
    def _is_agg_list(attr):
        if attr['@type'] in ['list']:
            if '@aggtype' in attr:
                if attr['@aggtype'] == "append":
                    return True
        return False

    def get_part(self, part, r_inst):
        # Get UVE and Type contents of given partition on given
        # collector/redis instance.
        uves = {}
        try:
            r_ip = r_inst[0]
            r_port = r_inst[1]
            rik = RedisInstKey(ip=r_ip,port=r_port)
            redish = self._redis_uve_map[rik].redis_handle
            gen_uves = {}
            for elems in redish.smembers("PART2KEY:" + str(part)):
                elems = convert_to_string(elems)
                info = elems.split(":", 5)
                gen = info[0] + ":" + info[1] + ":" + info[2] + ":" + info[3]
                typ = info[4]
                key = info[5]
                if not gen in gen_uves:
                     gen_uves[gen] = {}
                if not key in gen_uves[gen]:
                     gen_uves[gen][key] = {}
                gen_uves[gen][key][typ] = {}
        except Exception as e:
            self._logger.error("get_part failed %s for : %s:%d tb %s" \
                               % (str(e), r_ip, r_port, traceback.format_exc()))
        return r_ip + ":" + str(r_port) , gen_uves

    def get_tables(self):
        tables = set()
        for r_key, r_inst in self._redis_uve_map.items():
            if  r_inst.redis_handle is None or r_inst.collector_pid is None:
                continue
            else:
                redish = r_inst.redis_handle
            try:
                tbs = [convert_to_string(elem).split(":",1)[1] for elem in redish.keys("TABLE:*")]
                tables.update(set(tbs))
            except Exception as e:
                self._logger.error("get_tables failed %s for : (%s,%s) tb %s" \
                               % (str(e), str(r_key), str(r_inst.collector_pid),\
                                  traceback.format_exc()))

        return tables

    def get_uve(self, key, flat, filters=None, base_url=None):

        global more_than_100k
        filters = filters or {}
        sfilter = filters.get('sfilt')
        mfilter = filters.get('mfilt')
        tfilter = filters.get('cfilt')
        ackfilter = filters.get('ackfilt')
        if flat and not sfilter and not mfilter and self._usecache:
            return self._uvedbcache.get_uve(key, filters)

        is_alarm = False
        if tfilter == "UVEAlarms":
            is_alarm = True

        state = {}
        state[key] = {}
        rsp = {}
        failures = False

        tab = key.split(":",1)[0]

        for r_key, r_inst in self._redis_uve_map.items():
            if r_inst.redis_handle is None or r_inst.collector_pid is None:
                continue
            else:
                redish = r_inst.redis_handle
            try:
                qmap = {}

                ppe = redish.pipeline()
                ppe.smembers("ALARM_ORIGINS:" + key)
                if not is_alarm:
                    ppe.smembers("ORIGINS:" + key)
                pperes = ppe.execute()
                origins = set()
                for origset in pperes:
                    for smt in origset:
                        smt = convert_to_string(smt)
                        tt = smt.rsplit(":",1)[1]
                        sm = smt.rsplit(":",1)[0]
                        source = sm.split(":", 1)[0]
                        mdule = sm.split(":", 1)[1]
                        if tfilter is not None:
                            if tt not in tfilter:
                                continue
                        if sfilter is not None:
                            if sfilter != source:
                                continue
                        if mfilter is not None:
                            if mfilter != mdule:
                                continue
                        origins.add(smt)

                ppeval = redish.pipeline()
                for origs in origins:
                    ppeval.hgetall("VALUES:" + key + ":" + origs)
                odictlist = ppeval.execute()

                idx = 0
                for origs in origins:

                    odict = odictlist[idx]
                    idx = idx + 1

                    info = origs.rsplit(":", 1)
                    dsource = info[0]
                    typ = info[1]

                    afilter_list = set()
                    if tfilter is not None:
                        afilter_list = tfilter[typ]

                    del_uvealarms = False
                    for attr, value in odict.items():
                        attr = convert_to_string(attr)
                        value = convert_to_string(value)
                        if len(afilter_list):
                            if attr not in afilter_list:
                                continue

                        if value[0] == '<':
                            try:
                                #Adding this below If condition as part of CEM-11076
                                if len(value) >= 100000:
                                    more_than_100k += 1
                                    #Finding the sub_type of UVE
                                    start = value.find("<") + len("<")
                                    end = value.find(" ")
                                    sub_uve = value[start:end]
                                    self._logger.error("Dropping large UVE, from source %s and type %s and sub_type %s" \
                                        % (str(dsource), str(typ), str(sub_uve)))
                                    self._logger.debug("Count of UVE being dropped is %s" %str(more_than_100k))
                                    continue
                                snhdict = xmltodict.parse(value)
                            except:
                                self._logger.error("xml parsing failed key %s, struct %s: %s" \
                                    % (key, typ, str(value)))
                                continue

                            if snhdict[attr]['@type'] == 'list':
                                sname = ParallelAggregator.get_list_name(
                                        snhdict[attr])
                                if snhdict[attr]['list']['@size'] == '0':
                                    continue
                                elif snhdict[attr]['list']['@size'] == '1':
                                    if not isinstance(
                                        snhdict[attr]['list'][sname], list):
                                        snhdict[attr]['list'][sname] = [
                                            snhdict[attr]['list'][sname]]
                                if typ == 'UVEAlarms' and attr == 'alarms' and \
                                        ackfilter is not None:
                                    alarms = []
                                    for alarm in snhdict[attr]['list'][sname]:
                                        ack_attr = alarm.get('ack')
                                        if ack_attr:
                                            ack = ack_attr['#text']
                                        else:
                                            ack = 'false'
                                        if ack == ackfilter:
                                            alarms.append(alarm)
                                    if not len(alarms):
                                        del_uvealarms = True
                                        continue
                                    snhdict[attr]['list'][sname] = alarms
                                    snhdict[attr]['list']['@size'] = \
                                        str(len(alarms))
                        else:
                            continue

                        # print "Attr %s Value %s" % (attr, snhdict)
                        if typ not in state[key]:
                            state[key][typ] = {}
                        if attr not in state[key][typ]:
                            state[key][typ][attr] = {}
                        if dsource in state[key][typ][attr]:
                            self._logger.debug(\
                            "Found Dup %s:%s:%s:%s:%s = %s" % \
                                (key, typ, attr, source, mdule, state[
                                key][typ][attr][dsource]))
                        # To timestamp, we only keep latest source
                        if attr == '__T' and flat:
                            if len(state[key][typ][attr]) > 0:
                                if list(state[key][typ][attr].values())[0]['#text'] > snhdict[attr]['#text']:
                                    continue
                                else:
                                    state[key][typ][attr].clear()
                        state[key][typ][attr][dsource] = snhdict[attr]
                    if del_uvealarms and 'UVEAlarms' in state[key]:
                        del state[key]['UVEAlarms']

                pa = ParallelAggregator(state, self._uve_reverse_map)
                rsp = pa.aggregate(key, flat, base_url)
            except Exception as e:
                self._logger.error("redis-uve failed %s for key %s: (%s,%s) tb %s" \
                               % (str(e), key, str(r_key), str(r_inst.collector_pid),\
                                  traceback.format_exc()))
                failures = True
            else:
                self._logger.debug("Computed %s as %s" % (key,list(rsp.keys())))

        return failures, rsp
    # end get_uve

    def get_uve_regex(self, key):
        regex = ''
        if key[0] != '*':
            regex += '^'
        regex += key.replace('*', '.*?')
        if key[-1] != '*':
            regex += '$'
        return re.compile(regex)
    # end get_uve_regex

    def get_alarms(self, filters):
        tablesfilt = filters.get('tablefilt')
        kfilter = filters.get('kfilt')
        patterns = None
        if kfilter is not None:
            patterns = set()
            for filt in kfilter:
                patterns.add(self.get_uve_regex(filt))
        if self._usecache:
            rsp = self._uvedbcache.get_uve_list(tables, filters, patterns, False)
        else:
            tables = self.get_tables()
            rsp = {}
            for table in tables:
                uve_list = {}
                if tablesfilt is not None:
                    if table not in tablesfilt:
                        continue
                uve_keys = self.get_uve_list(table, filters, False)
                for uve_key in uve_keys:
                    _,uve_val = self.get_uve(
                        table + ':' + uve_key, True, filters)
                    if uve_val == {}:
                        continue
                    else:
                        uve_list[uve_key] = uve_val
                if len(uve_list):
                    rsp[table] = uve_list
        return rsp
    # end get_alarms

    def multi_uve_get(self, table, flat, filters=None, base_url=None):
        sfilter = filters.get('sfilt')
        mfilter = filters.get('mfilt')
        kfilter = filters.get('kfilt')

        patterns = None
        if kfilter is not None:
            patterns = set()
            for filt in kfilter:
                patterns.add(self.get_uve_regex(filt))

        if not sfilter and not mfilter and self._usecache:
            rsp = self._uvedbcache.get_uve_list([table], filters, patterns, False)
            if table in rsp:
                for uve_name in rsp[table]:
                    yield {'name': uve_name, 'value': rsp[table][uve_name]}
        else:
            # get_uve_list cannot handle attribute names very efficiently,
            # so we don't pass them here
            uve_list = self.get_uve_list(table, filters, False)

            for uve_name in uve_list:
                _,uve_val = self.get_uve(
                    table + ':' + uve_name, flat, filters,  base_url)
                if uve_val == {}:
                    continue
                else:
                    yield {'name': uve_name, 'value': uve_val}
    # end multi_uve_get

    def get_uve_list(self, table, filters=None, parse_afilter=False):
        is_alarm = False
        filters = filters or {}
        tfilter = filters.get('cfilt')
        if tfilter == "UVEAlarms":
            is_alarm = True
        uve_list = set()
        kfilter = filters.get('kfilt')
        sfilter = filters.get('sfilt')
        mfilter = filters.get('mfilt')

        patterns = None
        if kfilter is not None:
            patterns = set()
            for filt in kfilter:
                patterns.add(self.get_uve_regex(filt))

        if not sfilter and not mfilter and self._usecache:
            rsp = self._uvedbcache.get_uve_list([table], filters, patterns)
            if table in rsp:
                uve_list = rsp[table]
            return uve_list

        for r_key, r_inst in self._redis_uve_map.items():
            if  r_inst.redis_handle is None or r_inst.collector_pid is None:
                continue
            else:
                redish = r_inst.redis_handle
            try:
                # For UVE queries, we wanna read both UVE and Alarm table
                entries = redish.smembers('ALARM_TABLE:' + table)
                if not is_alarm:
                    entries = entries.union(redish.smembers('TABLE:' + table))
                for entry in entries:
                    entry = convert_to_string(entry)
                    info = (entry.split(':', 1)[1]).rsplit(':', 5)
                    uve_key = info[0]
                    if kfilter is not None:
                        kfilter_match = False
                        for pattern in patterns:
                            if pattern.match(uve_key):
                                kfilter_match = True
                                break
                        if not kfilter_match:
                            continue
                    src = info[1]
                    if sfilter is not None:
                        if sfilter != src:
                            continue
                    module = info[2]+':'+info[3]+':'+info[4]
                    if mfilter is not None:
                        if mfilter != module:
                            continue
                    typ = info[5]
                    if tfilter is not None:
                        if typ not in tfilter:
                            continue
                    if parse_afilter:
                        if tfilter is not None and len(tfilter[typ]):
                            valkey = "VALUES:" + table + ":" + uve_key + \
                                ":" + src + ":" + module + ":" + typ
                            for afilter in tfilter[typ]:
                                attrval = redish.hget(valkey, afilter)
                                if attrval is not None:
                                    break
                            if attrval is None:
                                continue
                    uve_list.add(uve_key)

            except Exception as e:
                self._logger.error("get_uve_list failed %s for : (%s,%s) tb %s" \
                               % (str(e), str(r_key), str(r_inst.collector_pid),\
                                  traceback.format_exc()))
        return uve_list
    # end get_uve_list

    def get_uvedb_cache_tables(self):
        if not self._usecache:
            return []
        return self._uvedbcache.get_uvedb_cache_tables()
    # end get_uvedb_cache_tables

    def get_uvedb_cache_table_keys(self, table):
        if not self._usecache:
            return []
        return self._uvedbcache.get_uvedb_cache_table_keys(table)
    # end get_uvedb_cache_table_keys

    def get_uvedb_cache_uve(self, table, uve_key):
        if not self._usecache:
            return None
        return self._uvedbcache.get_uvedb_cache_uve(table, uve_key)
    # end get_uvedb_cache_uve

    def get_active_collectors(self):
        return self._active_collectors
    # endif get_active_collectors

# end UVEServer


class ParallelAggregator(object):

    def __init__(self, state, rev_map = {}):
        self._state = state
        self._rev_map = rev_map

    def _default_agg(self, oattr):
        itemset = set()
        result = []
        for source in list(oattr.keys()):
            elem = oattr[source]
            hdelem = json.dumps(elem, sort_keys=True)
            if hdelem not in itemset:
                itemset.add(hdelem)
                result.append([elem, source])
            else:
                for items in result:
                    if elem in items:
                        items.append(source)
        return result

    def _is_elem_sum(self, oattr):
        akey = list(oattr.keys())[0]
        if oattr[akey]['@type'] not in ['i8', 'i16', 'i32', 'i64',
                                    'byte', 'u8', 'u16', 'u32', 'u64']:
            return False
        if '@aggtype' not in oattr[akey]:
            return False
        if oattr[akey]['@aggtype'] != "sum":
            return False
        return True

    def _is_struct_sum(self, oattr):
        akey = list(oattr.keys())[0]
        if oattr[akey]['@type'] != "struct":
            return False
        if '@aggtype' not in oattr[akey]:
            return False
        if oattr[akey]['@aggtype'] != "sum":
            return False
        return True

    def _is_list_union(self, oattr):
        akey = list(oattr.keys())[0]
        if not oattr[akey]['@type'] in ["list"]:
            return False
        if '@aggtype' not in oattr[akey]:
            return False
        if oattr[akey]['@aggtype'] in ["union"]:
            return True
        else:
            return False

    def _is_map_union(self, oattr):
        akey = list(oattr.keys())[0]
        if not oattr[akey]['@type'] in ["map"]:
            return False
        if '@aggtype' not in oattr[akey]:
            return False
        if oattr[akey]['@aggtype'] in ["union"]:
            return True
        else:
            return False

    def _is_append(self, oattr):
        akey = list(oattr.keys())[0]
        if not oattr[akey]['@type'] in ["list"]:
            return False
        if '@aggtype' not in oattr[akey]:
            return False
        if oattr[akey]['@aggtype'] in ["append"]:
            return True
        else:
            return False

    @staticmethod
    def get_list_name(attr):
        sname = ""
        for sattr in list(attr['list'].keys()):
            if sattr[0] not in ['@']:
                sname = sattr
        return sname

    @staticmethod
    def _get_list_key(elem):
        skey = ""
        for sattr in list(elem.keys()):
            if '@aggtype' in elem[sattr]:
                if elem[sattr]['@aggtype'] in ["listkey"]:
                    skey = sattr
        return skey

    def _struct_sum_agg(self, oattr):
        akey = list(oattr.keys())[0]
        result = copy.deepcopy(oattr[akey])
        sname = None
        for sattr in list(result.keys()):
            if sattr[0] != '@':
                sname = sattr
                break
        if not sname:
            return None
        cmap = {}
        for source,sval in oattr.items():
            for attr, aval in sval[sname].items():
                if aval['@type'] in ['i8',
                        'i16', 'i32', 'i64',
                        'byte', 'u8', 'u16', 'u32', 'u64']:
                    if attr not in cmap:
                        cmap[attr] = {}
                        cmap[attr]['@type'] = aval['@type']
                        cmap[attr]['#text'] = int(aval['#text'])
                    else:
                        cmap[attr]['#text'] += int(aval['#text'])
        for k,v in cmap.items():
            v['#text'] = str(v['#text'])
        result[sname] = cmap
        return result

    def _elem_sum_agg(self, oattr):
        akey = list(oattr.keys())[0]
        result = copy.deepcopy(oattr[akey])
        count = 0
        for source in list(oattr.keys()):
            count += int(oattr[source]['#text'])
        result['#text'] = str(count)
        return result

    def _list_union_agg(self, oattr):
        akey = list(oattr.keys())[0]
        result = {}
        for anno in list(oattr[akey].keys()):
            if anno[0] == "@":
                result[anno] = oattr[akey][anno]
        itemset = set()
        sname = ParallelAggregator.get_list_name(oattr[akey])
        result['list'] = {}
        result['list'][sname] = []
        result['list']['@type'] = oattr[akey]['list']['@type']
        siz = 0
        for source in list(oattr.keys()):
            if isinstance(oattr[source]['list'][sname], str):
                oattr[source]['list'][sname] = [oattr[source]['list'][sname]]
            for elem in oattr[source]['list'][sname]:
                hdelem = json.dumps(elem)
                if hdelem not in itemset:
                    itemset.add(hdelem)
                    result['list'][sname].append(elem)
                    siz += 1
        result['list']['@size'] = str(siz)

        return result

    def _map_union_agg(self, oattr):
        akey = list(oattr.keys())[0]
        result = {}
        for anno in list(oattr[akey].keys()):
            if anno[0] == "@":
                result[anno] = oattr[akey][anno]
        result['map'] = {}
        result['map']['@key'] = 'string'
        result['map']['@value'] = oattr[akey]['map']['@value']
        result['map']['element'] = []

        sname = None
        for ss in list(oattr[akey]['map'].keys()):
            if ss[0] != '@':
                if ss != 'element':
                    sname = ss
                    result['map'][sname] = []

        siz = 0
        for source in list(oattr.keys()):
            if sname is None:
                for subidx in range(0,int(oattr[source]['map']['@size'])):
                    print("map_union_agg Content %s" % (oattr[source]['map']))
                    result['map']['element'].append(source + ":" + \
                            json.dumps(oattr[source]['map']['element'][subidx*2]))
                    result['map']['element'].append(\
                            oattr[source]['map']['element'][(subidx*2) + 1])
                    siz += 1
            else:
                if not isinstance(oattr[source]['map']['element'], list):
                    oattr[source]['map']['element'] = [oattr[source]['map']['element']]
                if not isinstance(oattr[source]['map'][sname], list):
                    oattr[source]['map'][sname] = [oattr[source]['map'][sname]]

                for idx in range(0,int(oattr[source]['map']['@size'])):
                    result['map']['element'].append(source + ":" + \
                            json.dumps(oattr[source]['map']['element'][idx]))
                    result['map'][sname].append(\
                            oattr[source]['map'][sname][idx])
                    siz += 1

        result['map']['@size'] = str(siz)

        return result

    def _append_agg(self, oattr):
        akey = list(oattr.keys())[0]
        result = copy.deepcopy(oattr[akey])
        sname = ParallelAggregator.get_list_name(oattr[akey])
        result['list'][sname] = []
        siz = 0
        for source in list(oattr.keys()):
            if not isinstance(oattr[source]['list'][sname], list):
                oattr[source]['list'][sname] = [oattr[source]['list'][sname]]
            for elem in oattr[source]['list'][sname]:
                result['list'][sname].append(elem)
                siz += 1
        result['list']['@size'] = str(siz)
        return result

    @staticmethod
    def _list_agg_attrs(item):
        for ctrs in list(item.keys()):
            if '@aggtype'in item[ctrs]:
                if item[ctrs]['@aggtype'] in ["listkey"]:
                    continue
            if item[ctrs]['@type'] in ['i8', 'i16', 'i32', 'i64',
                                       'byte', 'u8', 'u16', 'u32', 'u64']:
                yield ctrs

    @staticmethod
    def consolidate_list(result, typ, objattr):
        applist = ParallelAggregator.get_list_name(
            result[typ][objattr])
        appkey = ParallelAggregator._get_list_key(
            result[typ][objattr]['list'][applist][0])

        # There is no listkey ; no consolidation is possible
        if len(appkey) == 0:
            return result

        # If the list's underlying struct has a listkey present,
        # we need to further aggregate entries that have the
        # same listkey
        mod_result = copy.deepcopy(result[typ][objattr])
        mod_result['list'][applist] = []
        res_size = 0
        mod_result['list']['@size'] = int(res_size)

        # Add up stats
        for items in result[typ][objattr]['list'][applist]:
            matched = False
            for res_items in mod_result['list'][applist]:
                if items[appkey]['#text'] in [res_items[appkey]['#text']]:
                    for ctrs in ParallelAggregator._list_agg_attrs(items):
                        res_items[ctrs]['#text'] += int(items[ctrs]['#text'])
                    matched = True
            if not matched:
                newitem = copy.deepcopy(items)
                for ctrs in ParallelAggregator._list_agg_attrs(items):
                    newitem[ctrs]['#text'] = int(items[ctrs]['#text'])
                mod_result['list'][applist].append(newitem)
                res_size += 1

        # Convert results back into strings
        for res_items in mod_result['list'][applist]:
            for ctrs in ParallelAggregator._list_agg_attrs(res_items):
                res_items[ctrs]['#text'] = str(res_items[ctrs]['#text'])
        mod_result['list']['@size'] = str(res_size)
        return mod_result

    def aggregate(self, key, flat, base_url = None):
        '''
        This function does parallel aggregation of this UVE's state.
        It aggregates across all sources and return the global state of the UVE
        '''
        result = {}
        ltyp = None
        objattr = None
        try:
            for typ in list(self._state[key].keys()):
                ltyp = typ
                result[typ] = {}
                for objattr in list(self._state[key][typ].keys()):
                    if self._is_elem_sum(self._state[key][typ][objattr]):
                        sume_res = self._elem_sum_agg(self._state[key][typ][objattr])
                        if flat:
                            result[typ][objattr] = \
                                OpServerUtils.uve_attr_flatten(sume_res)
                        else:
                            result[typ][objattr] = sume_res
                    elif self._is_struct_sum(self._state[key][typ][objattr]):
                        sums_res = self._struct_sum_agg(self._state[key][typ][objattr])
                        if flat:
                            result[typ][objattr] = \
                                OpServerUtils.uve_attr_flatten(sums_res)
                        else:
                            result[typ][objattr] = sums_res
                    elif self._is_list_union(self._state[key][typ][objattr]):
                        unionl_res = self._list_union_agg(
                            self._state[key][typ][objattr])
                        if flat:
                            result[typ][objattr] = \
                                OpServerUtils.uve_attr_flatten(unionl_res)
                        else:
                            result[typ][objattr] = unionl_res
                    elif self._is_map_union(self._state[key][typ][objattr]):
                        unionm_res = self._map_union_agg(
                            self._state[key][typ][objattr])
                        if flat:
                            result[typ][objattr] = \
                                OpServerUtils.uve_attr_flatten(unionm_res)
                        else:
                            result[typ][objattr] = unionm_res
                    elif self._is_append(self._state[key][typ][objattr]):
                        result[typ][objattr] = self._append_agg(
                            self._state[key][typ][objattr])
                        append_res = ParallelAggregator.consolidate_list(
                            result, typ, objattr)

                        if flat:
                            result[typ][objattr] =\
                                OpServerUtils.uve_attr_flatten(append_res)
                        else:
                            result[typ][objattr] = append_res

                    else:
                        default_res = self._default_agg(
                            self._state[key][typ][objattr])
                        if flat:
                            if (len(default_res) == 1):
                                result[typ][objattr] =\
                                    OpServerUtils.uve_attr_flatten(
                                        default_res[0][0])
                            else:
                                nres = []
                                for idx in range(len(default_res)):
                                    nres.append(default_res[idx])
                                    nres[idx][0] =\
                                        OpServerUtils.uve_attr_flatten(
                                            default_res[idx][0])
                                result[typ][objattr] = nres
                        else:
                            result[typ][objattr] = default_res
        except KeyError:
            pass
        except Exception as ex:
            print("Aggregation Error key %s type %s attr %s in %s" % \
                    (key, str(ltyp), str(objattr), str(self._state[key][typ][objattr])))
        return result

if __name__ == '__main__':
    uveserver = UVEServer(None, 0, None, None)
    gevent.spawn(uveserver.run())
    _, uve_state = json.loads(uveserver.get_uve("abc-corp:vn02", False))
    print(json.dumps(uve_state, indent=4, sort_keys=True))
