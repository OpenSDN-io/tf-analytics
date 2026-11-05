/*
 * Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
 * Copyright Contributors to the OpenSDN project
 */

#include "ruleglob.h"

#include <boost/python.hpp>

BOOST_PYTHON_MODULE(ruleengpy)
{
    using namespace boost::python;
    def("check_rulebuf", check_rulebuf);
}

