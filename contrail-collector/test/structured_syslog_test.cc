/*
 * Copyright (c) 2017 Juniper Networks, Inc. All rights reserved.
 */

#include <fstream>

#include <boost/assign/list_of.hpp>

#include <testing/gunit.h>

#include <base/logging.h>
#include <base/test/task_test_util.h>
#include <io/test/event_manager_test.h>
#include <io/io_types.h>
#include <io/udp_server.h>

#include "contrail-collector/db_handler.h"
#include "contrail-collector/structured_syslog_server.h"
#include "contrail-collector/structured_syslog_server_impl.h"
#include "generator.h"
#include "contrail-collector/syslog_collector.h"

using boost::assign::map_list_of;

namespace {
struct ArgSet {
    std::string statAttr;
    DbHandler::TagMap attribs_tag;
    DbHandler::AttribMap attribs;
};

class StatCbTester {
public:
    StatCbTester(const vector<ArgSet>& exp, bool log_exp = false) : exp_(exp) {
        for (vector<ArgSet>::const_iterator it = exp_.begin();
             it != exp_.end(); it++ ) {
            if (log_exp) {
                const ArgSet &exp_argSet(*it);
                LOG(ERROR, "Expected: " << it - exp_.begin());
                const std::string &statAttr(exp_argSet.statAttr);
                LOG(ERROR, "StatAttr: " << statAttr);
                const DbHandler::TagMap &attribs_tag(exp_argSet.attribs_tag);
                for (DbHandler::TagMap::const_iterator ct = attribs_tag.begin();
                     ct != attribs_tag.end(); ct++) {
                  LOG(ERROR, "tag " << ct->first);
                }
                const DbHandler::AttribMap &attribs(exp_argSet.attribs);
                for (DbHandler::AttribMap::const_iterator ct = attribs.begin();
                     ct != attribs.end(); ct++) {
                   LOG(ERROR, "attrib (" << ct->first << ", " << ct->second << ")");
                }
                LOG(ERROR, "\n");
            }
            match_.push_back(false);
        }
    }

    bool Compare(const DbHandler::TagMap & attribs_tag1,
                const DbHandler::TagMap & attribs_tag2,
                const DbHandler::AttribMap & attribs1,
                const DbHandler::AttribMap & attribs2) {
        for (DbHandler::TagMap::const_iterator ct = attribs_tag1.begin();
             ct != attribs_tag1.end(); ct++) {
                LOG(ERROR, "compare tag " << ct->first);
                bool found = false;
            for (DbHandler::TagMap::const_iterator ct2 = attribs_tag2.begin();
                 ct2 != attribs_tag2.end(); ct2++) {
                 if (ct->first == ct2->first) {
                 found = true;
                 LOG(ERROR, "compare tag found!!!" << ct->first);
                 break;
                 }
            }
            if (!found){
                LOG(ERROR, "compare tag NOT found!!!" << ct->first);
                return false;
            }

        }
        for (DbHandler::AttribMap::const_iterator ct = attribs1.begin();
             ct != attribs1.end(); ct++) {
            bool found = false;
            LOG(ERROR, "comparing attrib (" << ct->first << ", " << ct->second << ")");
            if (ct->first == "data.timestamp") {
                        LOG(ERROR, "SKIPPING timestamp comparison\n");
                        continue;
            }
            for (DbHandler::AttribMap::const_iterator ct2 = attribs2.begin();
                 ct2 != attribs2.end(); ct2++) {
                 if ((ct->first == ct2->first) && (ct->second == ct2->second)){
                    found = true;
                    LOG(ERROR, "attrib matched!!!" << ct->first);
                    break;
                 }
            }
            if (!found){
                LOG(ERROR, "attrib NOT matched!!!" << ct->first);
                return false;
            }
        }
        return true;
    }

    void Cb(const uint64_t &timestamp,
            const std::string& statName,
            const std::string& statAttr,
            const DbHandler::TagMap & attribs_tag,
            const DbHandler::AttribMap & attribs) {
        bool is_match = false;
        LOG(ERROR, "StatName: " << statName << " StatAttr: " << statAttr);
        for (DbHandler::TagMap::const_iterator ct = attribs_tag.begin();
             ct != attribs_tag.end(); ct++) {
            LOG(ERROR, "tag " << ct->first);
        }
        for (DbHandler::AttribMap::const_iterator ct = attribs.begin();
             ct != attribs.end(); ct++) {
            LOG(ERROR, "attrib (" << ct->first << ", " << ct->second << ")");
        }
        for (size_t idx = 0 ; idx < exp_.size() ; idx ++) {
            if ((exp_[idx].statAttr == statAttr) &&
                Compare(exp_[idx].attribs_tag, attribs_tag, exp_[idx].attribs, attribs)) {
                EXPECT_EQ(match_[idx] , false);
                match_[idx] = true;
                is_match = true;
                LOG(ERROR, "MATCHED");
                break;
            }
        }
        LOG(ERROR, "\n");
        EXPECT_EQ(true, is_match);
    }

    void Verify(){
        LOG(ERROR, "Verifying....");
        for (vector<bool>::const_iterator it = match_.begin();
             it != match_.end(); it++ ) {
            EXPECT_EQ(true, *it);
        }
    }

    virtual ~StatCbTester() {
    }
    const vector<ArgSet> exp_;
    vector<bool> match_;
};

vector<ArgSet> PopulateTestMessageStatsInfo(bool no_struct_part) {
    vector<ArgSet> av;
    ArgSet a1;
    a1.statAttr = string("data");
    uint64_t ts = 1481978789000018;
    if (no_struct_part) {
        a1.attribs = map_list_of
            ("Source", DbHandler::Var("syslog-hostname"))
            ("data.ip", DbHandler::Var("127.0.0.1"))
            ("data.prog", DbHandler::Var("RT_FLOW"))
            ("data.hostname", DbHandler::Var("syslog-hostname"))
            ("data.timestamp", DbHandler::Var(ts))
                .convert_to_container<map<string, DbHandler::Var> >();
    } else {
        a1.attribs = map_list_of
            ("Source", DbHandler::Var("syslog-hostname"))
            ("data.ip", DbHandler::Var("127.0.0.1"))
            ("data.prog", DbHandler::Var("RT_FLOW"))
            ("data.hostname", DbHandler::Var("syslog-hostname"))
            ("data.hardware", DbHandler::Var("junos@2636.1.1.1.2.26"))
            ("data.tag", DbHandler::Var("APPTRACK_SESSION_CLOSE"))
            ("data.timestamp", DbHandler::Var(ts))
            ("data.source-address", DbHandler::Var("4.0.0.1"))
            ("data.source-port", DbHandler::Var(static_cast<uint64_t>(13175)))
            ("data.reason", DbHandler::Var("TCP RST"))
                .convert_to_container<map<string, DbHandler::Var> >();
    }
    DbHandler::AttribMap sm;
    a1.attribs_tag.insert(make_pair("Source", make_pair(
        DbHandler::Var("127.0.0.1"), sm)));
    a1.attribs_tag.insert(make_pair("data.hostname", make_pair(
        DbHandler::Var("syslog-hostname"), sm)));
    if (!no_struct_part) {
        a1.attribs_tag.insert(make_pair("data.tag", make_pair(
        DbHandler::Var("APPTRACK_SESSION_CLOSE"), sm)));
        a1.attribs_tag.insert(make_pair("data.source-address", make_pair(
            DbHandler::Var("4.0.0.1"), sm)));
        }
    av.push_back(a1);
    return av;
}

class StructuredSyslogStatWalkerTest : public ::testing::Test {
};

TEST_F(StructuredSyslogStatWalkerTest, Basic) {
    StatCbTester ct(PopulateTestMessageStatsInfo(false), true);
    const std::string test_structured_syslog ("<14>Dec 17 14:46:29 syslog-hostname RT_FLOW: APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\"]");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    ASSERT_TRUE(r);
    if (r ==false) {
        ct.Verify();
    }

}

TEST_F(StructuredSyslogStatWalkerTest, DeviceMultiSyslog) {
    StatCbTester ct(PopulateTestMessageStatsInfo(false), true);
    const std::string test_structured_syslog ("648 <14>1 2017-02-03T09:05:26.178Z syslog-hostname RT_FLOW - APPTRACK_SESSION_CREATE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\" destination-address=\"172.217.26.161\" destination-port=\"443\" service-name=\"junos-https\" application=\"UNKNOWN\" nested-application=\"UNKNOWN\" nat-source-address=\"10.213.17.220\" nat-source-port=\"10831\" nat-destination-address=\"172.217.26.161\" nat-destination-port=\"443\" src-nat-rule-name=\"r1\" dst-nat-rule-name=\"N/A\" protocol-id=\"6\" policy-name=\"default-permit\" source-zone-name=\"trust\" destination-zone-name=\"trust\" session-id-32=\"271992\" username=\"N/A\" roles=\"N/A\" encrypted=\"UNKNOWN\"]639 <14>1 2017-02-03T09:05:27.776Z syslog-hostname RT_FLOW - APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\" destination-address=\"10.209.194.133\" destination-port=\"53\" service-name=\"junos-dns-udp\" application=\"DNS\" nested-application=\"UNKNOWN\" nat-source-address=\"10.213.17.220\" nat-source-port=\"14299\" nat-destination-address=\"10.209.194.133\" nat-destination-port=\"53\" src-nat-rule-name=\"r1\" dst-nat-rule-name=\"N/A\" protocol-id=\"17\" policy-name=\"default-permit\" source-zone-name=\"trust\" destination-zone-name=\"trust\" session-id-32=\"272036\" username=\"N/A\" roles=\"N/A\" encrypted=\"No\"]");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    ASSERT_TRUE(r);
    if (r ==false) {
        ct.Verify();
    }

}
TEST_F(StructuredSyslogStatWalkerTest, DeviceSyslog) {
    StatCbTester ct(PopulateTestMessageStatsInfo(false), true);
    const std::string test_structured_syslog ("<14>1 2016-12-17T14:46:29.585Z syslog-hostname RT_FLOW - APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\"]");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    ASSERT_TRUE(r);
    if (r ==false) {
        ct.Verify();
    }

}

TEST_F(StructuredSyslogStatWalkerTest, DeviceSyslogTz) {
    StatCbTester ct(PopulateTestMessageStatsInfo(false), true);
    const std::string test_structured_syslog ("<14>1 2016-12-17T19:46:29.585+5:00 syslog-hostname RT_FLOW - APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\"]");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    ASSERT_TRUE(r);
    if (r ==false) {
        ct.Verify();
    }

}

TEST_F(StructuredSyslogStatWalkerTest, ParseError) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    const std::string test_structured_syslog ("ABCD <14>Dec 17 14:46:29 syslog-hostname RT_FLOW: APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\"]");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    //ASSERT_FALSE(r);
    ASSERT_TRUE(r);

}

TEST_F(StructuredSyslogStatWalkerTest, BadStruct) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    const std::string test_structured_syslog ("<14>Dec 17 14:46:29 syslog-hostname RT_FLOW: APPTRACK_SESSION_CLOSE [junos@2636.1.1.1.2.26 reason=\"TCP RST\" source-address=\"4.0.0.1\" source-port=\"13175\"");
    boost::system::error_code ec;
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p, test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        boost::shared_ptr<std::string>());
    delete config_obj;
    ASSERT_TRUE(r);

}

TEST_F(StructuredSyslogStatWalkerTest, MessageLengthSplit_1) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    std::string test_structured_syslog;
    std::string test_structured_syslog_rest_of_buffer;
    std::ifstream test_structured_syslog_File;
    test_structured_syslog_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_MsgLenSplit_1.txt");
    std::getline (test_structured_syslog_File, test_structured_syslog);
 
    std::ifstream test_structured_syslog_rest_of_buffer_File;
    test_structured_syslog_rest_of_buffer_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_Buff_MsgLenSplit_1.txt");
    std::getline (test_structured_syslog_rest_of_buffer_File, 
        test_structured_syslog_rest_of_buffer);

    test_structured_syslog_rest_of_buffer_File.close();
    test_structured_syslog_File.close();

    boost::system::error_code ec;
    boost::shared_ptr<std::string> sess_buf;
    sess_buf.reset(new std::string(""));
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p =
        reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    const uint8_t* p_rest_of_buffer =
        reinterpret_cast<const uint8_t*>
        (test_structured_syslog_rest_of_buffer.c_str());
    //boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r1 =
        structured_syslog::impl::ProcessStructuredSyslog(p,
        test_structured_syslog.length(),rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    if (*sess_buf != "1") {
        r1 = false;
        LOG(ERROR, "Expected: 1, Error: \"incorrect value in session buffer\" ");
    }
    bool r2 = structured_syslog::impl::ProcessStructuredSyslog(p_rest_of_buffer,
        test_structured_syslog_rest_of_buffer.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    sess_buf->clear();
    delete config_obj;
    bool r = r1*r2;
    ASSERT_TRUE(r);
}





TEST_F(StructuredSyslogStatWalkerTest, MessageLengthSplit_2) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    std::string test_structured_syslog;
    std::string test_structured_syslog_rest_of_buffer;
    std::ifstream test_structured_syslog_File;
    test_structured_syslog_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_MsgLenSplit_2.txt");
    std::getline (test_structured_syslog_File, test_structured_syslog);
 
    std::ifstream test_structured_syslog_rest_of_buffer_File;
    test_structured_syslog_rest_of_buffer_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_Buff_MsgLenSplit_2.txt");
    std::getline (test_structured_syslog_rest_of_buffer_File, 
        test_structured_syslog_rest_of_buffer);

    test_structured_syslog_rest_of_buffer_File.close();
    test_structured_syslog_File.close();
    boost::system::error_code ec;
    boost::shared_ptr<std::string> sess_buf;
    sess_buf.reset(new std::string(""));
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p = 
        reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    const uint8_t* p_rest_of_buffer = 
        reinterpret_cast<const uint8_t*>(test_structured_syslog_rest_of_buffer.c_str());
    //boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r1 = structured_syslog::impl::ProcessStructuredSyslog(p, 
        test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    if (*sess_buf != "10") {
        r1 = false;
        LOG(ERROR, "Expected: 10, Error: \"incorrect value in session buffer\" ");
    }
    bool r2 = structured_syslog::impl::ProcessStructuredSyslog(p_rest_of_buffer, 
        test_structured_syslog_rest_of_buffer.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    sess_buf->clear();
    delete config_obj;
    bool r = r1*r2;
    ASSERT_TRUE(r);
}



TEST_F(StructuredSyslogStatWalkerTest, MessageLengthSplit_3) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    std::string test_structured_syslog;
    std::string test_structured_syslog_rest_of_buffer;
    std::ifstream test_structured_syslog_File;
    test_structured_syslog_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_MsgLenSplit_3.txt");
    std::getline (test_structured_syslog_File, test_structured_syslog);
 
    std::ifstream test_structured_syslog_rest_of_buffer_File;
    test_structured_syslog_rest_of_buffer_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_Buff_MsgLenSplit_3.txt");
    std::getline (test_structured_syslog_rest_of_buffer_File, 
        test_structured_syslog_rest_of_buffer);

    test_structured_syslog_rest_of_buffer_File.close();
    test_structured_syslog_File.close();
    boost::system::error_code ec;
    boost::shared_ptr<std::string> sess_buf;
    sess_buf.reset(new std::string(""));
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p =
        reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    const uint8_t* p_rest_of_buffer =
        reinterpret_cast<const uint8_t*>(test_structured_syslog_rest_of_buffer.c_str());
    //boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r1 = structured_syslog::impl::ProcessStructuredSyslog(p,
        test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    if (*sess_buf != "1011") {
        r1 = false;
        LOG(ERROR, "Expected: 1011, Error: \"incorrect value in session buffer\" ");
    }
    bool r2 = structured_syslog::impl::ProcessStructuredSyslog(p_rest_of_buffer,
        test_structured_syslog_rest_of_buffer.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    sess_buf->clear();
    delete config_obj;
    bool r = r1*r2;
    ASSERT_TRUE(r);
}


TEST_F(StructuredSyslogStatWalkerTest, MessageLengthBad) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);
    std::string test_structured_syslog ;
    std::string test_structured_syslog_rest_of_buffer ;
    std::ifstream test_structured_syslog_File;
    test_structured_syslog_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_MsgLenBad.txt");
    std::getline (test_structured_syslog_File, test_structured_syslog);
    std::ifstream test_structured_syslog_rest_of_buffer_File;
    test_structured_syslog_rest_of_buffer_File.open(
        "./src/contrail-analytics/contrail-collector/test/"
        "test_StructSys_Buff_MsgLenBad.txt");
    std::getline (test_structured_syslog_rest_of_buffer_File,
        test_structured_syslog_rest_of_buffer);
    test_structured_syslog_rest_of_buffer_File.close();
    test_structured_syslog_File.close();

    boost::system::error_code ec;
    boost::shared_ptr<std::string> sess_buf;
    sess_buf.reset(new std::string(""));
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p =
        reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    const uint8_t* p_rest_of_buffer =
        reinterpret_cast<const uint8_t*>(
            test_structured_syslog_rest_of_buffer.c_str());
    //boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r1 = structured_syslog::impl::ProcessStructuredSyslog(p, 
        test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    bool r2 = structured_syslog::impl::ProcessStructuredSyslog(p_rest_of_buffer,
        test_structured_syslog_rest_of_buffer.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    sess_buf->clear();
    delete config_obj;
    bool r = !(r1)*r2;
    ASSERT_TRUE(r);
}

TEST_F(StructuredSyslogStatWalkerTest, SNMP_TRAPSyslog) {
    StatCbTester ct(PopulateTestMessageStatsInfo(true), true);

    std::string test_structured_syslog ;
    std::string test_structured_syslog_rest_of_buffer ;
    std::ifstream test_structured_syslog_File;
    test_structured_syslog_File.open(
        "./src/contrail-analytics/contrail-collector/test/test_StructSys_SNMP.txt");
    std::getline (test_structured_syslog_File, test_structured_syslog); 
    test_structured_syslog_File.close();

    boost::system::error_code ec;
    boost::shared_ptr<std::string> sess_buf;
    sess_buf.reset(new std::string(""));
    boost::asio::ip::address raddr(
        boost::asio::ip::address::from_string("127.0.0.1", ec));
    boost::asio::ip::udp::endpoint rep(raddr, 0);
    const uint8_t* p =
        reinterpret_cast<const uint8_t*>(test_structured_syslog.c_str());
    //boost::scoped_ptr<EventManager> evm_(new EventManager());
    StructuredSyslogConfig *config_obj = new StructuredSyslogConfig(NULL, 1);
    bool r = structured_syslog::impl::ProcessStructuredSyslog(p,
        test_structured_syslog.length(), rep.address(),
        boost::bind(&StatCbTester::Cb, &ct, _1, _2, _3, _4, _5), config_obj,
        boost::shared_ptr<structured_syslog::StructuredSyslogForwarder>(),
        sess_buf);
    sess_buf->clear();
    delete config_obj;
    ASSERT_TRUE(r);

}

}

int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    LoggingInit();
    int result = RUN_ALL_TESTS();
    return result;
}
