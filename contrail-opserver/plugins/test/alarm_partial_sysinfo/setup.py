#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

from setuptools import setup, find_packages

setup(
    name='alarm_partial_sysinfo',
    version='0.1.dev0',
    packages=find_packages(),
    entry_points = {
        'contrail.analytics.alarms': [
            'ObjectAnalyticsAlarmInfo = alarm_partial_sysinfo.main:PartialSysinfoAnalytics',
            'ObjectVRouter = alarm_partial_sysinfo.main:PartialSysinfoCompute',
            'ObjectConfigNode = alarm_partial_sysinfo.main:PartialSysinfoConfig',
            'ObjectBgpRouter = alarm_partial_sysinfo.main:PartialSysinfoControl',
        ],
    },
    zip_safe=False,
    long_description="PartialSysinfo Alarm"
)
