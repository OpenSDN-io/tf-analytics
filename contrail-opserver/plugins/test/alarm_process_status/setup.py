#
# Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
#

from setuptools import setup, find_packages

setup(
    name='alarm_process_status',
    version='0.1.dev0',
    packages=find_packages(),
    entry_points = {
        'contrail.analytics.alarms': [
            'ObjectCollectorInfo = alarm_process_status.main:ProcessStatus',
            'ObjectAnalyticsAlarmInfo = alarm_process_status.main:ProcessStatus',
            'ObjectAnalyticsSNMPInfo = alarm_process_status.main:ProcessStatus',
            'ObjectVRouter = alarm_process_status.main:ProcessStatus',
            'ObjectConfigNode = alarm_process_status.main:ProcessStatus',
            'ObjectBgpRouter = alarm_process_status.main:ProcessStatus',
            'ObjectDatabaseInfo = alarm_process_status.main:ProcessStatus',
        ],
    },
    zip_safe=False,
    long_description="ProcessStatus Alarm"
)
