#
# Copyright (c) 2015 Juniper Networks, Inc. All rights reserved.
#
import re, setuptools


def requirements(filename):
    with open(filename) as f:
        lines = f.read().splitlines()
    c = re.compile(r'\s*#.*')
    return list(filter(bool, map(lambda y: c.sub('', y).strip(), lines)))

setuptools.setup(
    name='tf_topology',
    version='0.1.0',
    description='tungstenfabric topology package.',
    long_description=open('README.txt').read(),
    packages=setuptools.find_packages(),

    # metadata
    author="OpenContrail",
    author_email="dev@lists.opencontrail.org",
    license="Apache Software License",
    url="http://www.opencontrail.org/",

    install_requires=requirements('requirements.txt'),
    entry_points = {
        'console_scripts' : [
            'tf-topology = tf_topology.main:main',
        ],
    },
)
