#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

# -*- mode: python; -*-
# src directory

import platform
import subprocess
import sys

Import('contrail_common_base_doc_files')
Import('contrail_common_io_doc_files')
#requires chnage in controller/src/SConscript
#Analytics section: controller/src/analytics/analytics.sandesh
Import('controller_vns_sandesh_doc_files')

subdirs_no_dup = [
          'contrail-collector',
          'contrail-query-engine',
          'contrail-opserver',
           ]

subdirs_dup = [
          'tf-snmp-collector',
          'tf-topology'
           ]

variant_dir_map = {}
variant_dir_map['contrail-collector'] = 'analytics'
variant_dir_map['contrail-query-engine'] = 'query_engine'
variant_dir_map['tf-snmp-collector'] = 'tf-snmp-collector'
variant_dir_map['tf-topology'] = 'tf-topology'
variant_dir_map['contrail-opserver'] = 'opserver'

include = ['#/src/contrail-analytics', '#/build/include', '#src/contrail-common', '#controller/lib']
libpath = ['#/build/lib']
libs = ['boost_system', 'log4cplus', 'pthread', 'tbb']

common = DefaultEnvironment().Clone()
common.Append(LIBPATH = libpath)
common.Prepend(LIBS = libs)

common.Append(CCFLAGS = ['-Wall', '-Werror', '-Wsign-compare'])

gpp_version = subprocess.check_output(
    "g++ --version | grep g++ | awk '{print $3}'",
    shell=True, env={}).rstrip()
if isinstance(gpp_version, bytes):
    gpp_version = gpp_version.decode()
gpp_version_major = int(gpp_version.split(".")[0])
if gpp_version == "4.8.5" or gpp_version_major >= 8:
    common.Append(CCFLAGS =['-Wno-narrowing', '-Wno-conversion-null'])
    if gpp_version_major >= 8:
        # auto_ptr is depricated - dont error on deprication warnings
        common.Append(CCFLAGS = ['-Wno-error=deprecated-declarations', '-Wno-deprecated-declarations'])

if platform.system().startswith('Linux'):
    common.Append(CCFLAGS = ['-Wno-unused-local-typedefs'])
common.Append(CPPPATH = include)
common.Append(CCFLAGS = ['-DRAPIDJSON_NAMESPACE=contrail_rapidjson'])

BuildEnv = common.Clone()

if sys.platform.startswith('linux'):
    BuildEnv.Append(CCFLAGS = ['-DLINUX'])

#
# Message documentation for common modules
#

# base
BuildEnv['BASE_DOC_FILES'] = contrail_common_base_doc_files

# IO
BuildEnv['IO_DOC_FILES'] = contrail_common_io_doc_files

# SANDESH
BuildEnv['VNS_SANDESH_DOC_FILES'] = controller_vns_sandesh_doc_files

# Analytics (contrail-collector)
contrail_collector_doc_files = []
analytics_doc_target = common['TOP'] + '/' + variant_dir_map['contrail-collector'] + '/'
contrail_collector_doc_files += common.SandeshGenDoc('#src/contrail-analytics/contrail-collector/analytics.sandesh', analytics_doc_target)
contrail_collector_doc_files += common.SandeshGenDoc('#src/contrail-analytics/contrail-collector/viz.sandesh', analytics_doc_target)
contrail_collector_doc_files += common.SandeshGenDoc('#src/contrail-analytics/contrail-collector/redis.sandesh', analytics_doc_target)
BuildEnv['ANALYTICS_DOC_FILES'] = contrail_collector_doc_files
#Export('contrail_collector_doc_files')


BuildEnv['INSTALL_DOC_PKG'] = BuildEnv['INSTALL_DOC'] + '/contrail-docs/html'
BuildEnv['INSTALL_MESSAGE_DOC'] = BuildEnv['INSTALL_DOC_PKG'] + '/messages'


for dir in subdirs_no_dup:
    BuildEnv.SConscript(dir + '/SConscript',
                         exports='BuildEnv',
                         variant_dir=BuildEnv['TOP'] + '/' + variant_dir_map[dir],
                         duplicate=0)

for dir in subdirs_dup:
    BuildEnv.SConscript(dirs=[dir],
                         exports='BuildEnv',
                         variant_dir=BuildEnv['TOP'] + '/' + variant_dir_map[dir],
                         duplicate=1)

#AnalyticsEnv.SConscript(dirs=['tf-snmp-collector'],
#        exports='AnalyticsEnv',
#        variant_dir=BuildEnv['TOP'] + '/tf-snmp-collector',
#        duplicate=1)

#AnalyticsEnv.SConscript(dirs=['tf-topology'],
#        exports='AnalyticsEnv',
#        variant_dir=BuildEnv['TOP'] + '/tf-topology',
#        duplicate=1)
