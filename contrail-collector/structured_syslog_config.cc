/*
 * Copyright (c) 2017 Juniper Networks, Inc. All rights reserved.
 */


#include <sstream>
#include <boost/make_shared.hpp>
#include <boost/asio.hpp>
#include <analytics/analytics_types.h>
#include "base/regex.h"
#include "analytics_types.h"
#include "structured_syslog_config.h"
#include "options.h"
#include <base/logging.h>
#include <boost/bind.hpp>
#include "rapidjson/document.h"
#include "rapidjson/writer.h"
#include "rapidjson/stringbuffer.h"

using boost::regex_error;
using contrail::regex;
using contrail::regex_match;
using contrail::regex_search;

StructuredSyslogConfig::StructuredSyslogConfig(ConfigClientCollector *config_client, uint64_t structured_syslog_active_session_map_limit) {
    activeSessionConfigMapLIMIT = structured_syslog_active_session_map_limit;
    LOG(INFO, "StructuredSyslogConfig:Num of active session LIMIT in session config map: " << activeSessionConfigMapLIMIT);
    if (config_client) {
        config_client->RegisterConfigReceive("structured-systemlog", boost::bind(
                                 &StructuredSyslogConfig::ReceiveConfig, this, _1, _2));
    }
}

StructuredSyslogConfig::~StructuredSyslogConfig() {
    hostname_records_.erase(hostname_records_.begin(),
                            hostname_records_.end());
    tenant_records_.erase(tenant_records_.begin(),
                            tenant_records_.end());
    application_records_.erase(application_records_.begin(),
                               application_records_.end());
    tenant_application_records_.erase(tenant_application_records_.begin(),
                                      tenant_application_records_.end());
    networks_map_.erase(networks_map_.begin(), networks_map_.end());
    session_config_map_.erase(session_config_map_.begin(),
                                session_config_map_.end());
}


bool 
StructuredSyslogConfig::AddSyslogSessionCounter(const std::string session_unique_key, 
                                                std::map<std::string, uint64_t> session_traffic_counters) {
    try {
        SyslogSessionConfig::iterator it = session_config_map_.find(session_unique_key);
        if (it != session_config_map_.end()) {
            LOG(DEBUG, "Session key found in session counter map.");
        }
        else {
            LOG(DEBUG, "Session key NOT found in session counter map.");
            //Put a Limit to num of session entries in the map.
            if(session_config_map_.size() >= activeSessionConfigMapLIMIT) {
                LOG(ERROR, "active sessions Config Map LIMIT reached. Current active session count: "<< session_config_map_.size());
                return false;
            }
        }
        LOG(DEBUG, "Adding/Replacing session traffic counters for session key " << session_unique_key);
        session_config_map_[session_unique_key] = session_traffic_counters;
        return true;
    }
    catch (std::exception &e) {
        LOG(ERROR, "Adding session traffic counters failed for session key: " << session_unique_key);
        return false;
    }
 }

int 
StructuredSyslogConfig::RemoveSyslogSessionCounter(const std::string &session_unique_key) {
    try {
        LOG(DEBUG, "Removing Syslog Session Counter for " << session_unique_key);
        return session_config_map_.erase(session_unique_key);
    }
    catch (std::exception &e) {
        LOG(ERROR, "Removing session traffic counters failed for session key: " << session_unique_key);
        return 0;
    }
}

bool
StructuredSyslogConfig::FetchSyslogSessionCounters(const std::string &session_unique_key,
                                                    std::map<std::string, uint64_t> &session_traffic_counters) {
    try {
        SyslogSessionConfig::iterator itr = session_config_map_.find(session_unique_key);
        if (itr != session_config_map_.end()) {
            session_traffic_counters = itr->second;
            return true;
        }
        else{
            LOG(DEBUG, "Session counters not found for session key: " << session_unique_key);
            return false;
        }
    }
    catch (std::exception &e) {
        LOG(ERROR, "ERROR in fetching Session traffic counters for session key: " << session_unique_key);
        return false;
    }
}

/*  Return int 4 when IP belongs to protocol IPv4 or
    Return int 6 when IP belongs to protocol IPv6, otherwise
    Return int -1 if IP is not valid */
int
StructuredSyslogConfig::get_ip_version (const std::string ip) {
    int version = -1;
    try {
        boost::asio::ip::address addr = boost::asio::ip::address::from_string(ip);
        if (addr.is_v4())
            version = 4;
        else if (addr.is_v6())
            version = 6;
    }
    catch (std::exception &e){
        LOG(ERROR, "IP : "<< ip <<" found while checking IP protocol is invalid. ERROR: " << e.what());
    }
    return version;
}

uint32_t
StructuredSyslogConfig::IPToUInt(std::string ip) {
    int a, b, c, d;
    uint32_t addr = 0;

    if (sscanf(ip.c_str(), "%d.%d.%d.%d", &a, &b, &c, &d) != 4)
        return 0;

    addr = a << 24;
    addr |= b << 16;
    addr |= c << 8;
    addr |= d;
    return addr;
}

std::vector<std::string>
StructuredSyslogConfig::split_into_vector(std::string  str, char delimiter) {
    std::vector<std::string> list;
    std::stringstream ss(str);
    std::string s;
    while(getline(ss, s, delimiter)){
        list.push_back(s);
    }
    return list;
}



bool
StructuredSyslogConfig::AddNetwork(const std::string& key, const std::string& network, const std::string& mask, const std::string& location)
{
    uint32_t network_addr = IPToUInt(network);
    uint32_t mask_addr = IPToUInt(mask);

    uint32_t net_lower = (network_addr & mask_addr);
    uint32_t net_upper = (net_lower | (~mask_addr));

    std::string id = location;
    IPNetwork net(net_lower, net_upper, id);
    boost::mutex::scoped_lock lock(networks_map_refresh_mutex);

    IPNetworks_map::iterator it = networks_map_.find(key);
    if (it  != networks_map_.end()) {
        LOG(DEBUG, "VPN name found in Networks MAP while adding network. Appending to existing values of VPN key... ");
        //sorted insertion into vector
        it->second.insert(std::upper_bound(it->second.begin(), it->second.end(), net), net);
        LOG(DEBUG, "IPNetwork with destination address " << network_addr << " added in networks_map with VPN key " << key);
    } else {
        LOG(DEBUG, "VPN name NOT found in Networks MAP while adding network. Creating a new entry in Networks MAP ...");
        IPNetworks  new_network;
        new_network.push_back(net);
        networks_map_.insert(std::make_pair(key, new_network) );
        LOG(DEBUG, "IPNetwork with destination address " << network_addr << " added in networks_map with VPN key " << key);
    }
    return true;
}


bool
StructuredSyslogConfig::RefreshNetworksMap(const std::string location){

    boost::mutex::scoped_lock lock(networks_map_refresh_mutex);
    for(IPNetworks_map::iterator it = networks_map_.begin(); it != networks_map_.end(); it++){
        std::vector<int> indexes_to_be_deleted;
        for(IPNetworks::iterator i = it->second.begin(); i != it->second.end(); i++) {
            if (location == i->id){
                LOG(DEBUG, "Location " << i->id << " to be deleted from Networks MAP with VPN " << it->first );
                indexes_to_be_deleted.push_back(i - it->second.begin());
            }
        }
        for(std::vector<int>::reverse_iterator v = indexes_to_be_deleted.rbegin(); v != indexes_to_be_deleted.rend(); ++v) {
            IPNetworks::iterator i = it->second.begin();
            it->second.erase(*v + i);
        }
    }
    LOG(INFO, "Networks MAP Refreshed!" );
    return true;
}

std::string
StructuredSyslogConfig::FindNetwork(std::string ip,  std::string key, std::string src_location)
{
    uint32_t network_addr = IPToUInt(ip);
    IPNetwork ip_network(network_addr, 0, ip);
    std::string unknown_location;

    IPNetworks_map::iterator it = networks_map_.find(key);
    if (it  != networks_map_.end()) {
        IPNetworks::iterator upper = std::upper_bound(it->second.begin(), it->second.end(), ip_network);

        uint32_t idx = upper - it->second.begin();
        if (idx <= it->second.size() && idx != 0){
            IPNetwork found_network_obj(0, 0, unknown_location);
            uint32_t min_range = UINT32_MAX;
            //check for last idx (idx-1) before upper_bound idx.
            //check for overlapping IP ranges in loop.
            //network with min. range should take priority.
            for (int i = idx - 1; i >= 0; i--) {
                IPNetwork possible_network_obj = it->second[i];
                LOG(DEBUG, "Checking for possible network-range match location " << possible_network_obj.id);
                uint32_t possible_network_range = possible_network_obj.address_end - possible_network_obj.address_begin;
                if ((network_addr >= possible_network_obj.address_begin)
                    && (network_addr <= possible_network_obj.address_end)) {
                    if (possible_network_range < min_range) {
                        if (possible_network_obj.id != src_location) {
                            found_network_obj = possible_network_obj;
                            min_range = possible_network_range;
                            LOG(DEBUG, "Possible Network found for " << ip << " from Tenant::VPN " <<  key << 
                                " in Site : " << found_network_obj.id  << " with range : " << possible_network_range);
                        }
                    }
                }

            }
            if (!(found_network_obj.id.empty())) {
                LOG(DEBUG, "Network found for " << ip << " from Tenant::VPN " <<  key << 
                    " in Site : " << found_network_obj.id );
                return found_network_obj.id;
            }
            else {
                LOG(DEBUG,"Network address "<< ip <<" doesnt not belong to the found range " 
                    << found_network_obj.address_begin << " - " << found_network_obj.address_end << " in Tenant::VPN " << key);
            }
        }
        else{
            LOG(DEBUG,"Network range not found for " << ip << " in Tenant::VPN " << key );
        }
    }
    else {
        LOG(DEBUG, "Tenant::VPN "<< key << " NOT found in Network MAP!");
    }
    return unknown_location;
 }

void
StructuredSyslogConfig::HostnameRecordsHandler(const contrail_rapidjson::Document &jdoc,
                                               bool add_update) {
    if (jdoc.IsObject() && jdoc.HasMember("structured_syslog_hostname_record")) {
        const contrail_rapidjson::Value& hr = jdoc["structured_syslog_hostname_record"];
        std::string name, hostaddr, tenant, location, device, tags;
        std::map< std::string, std::string > linkmap;
        bool location_exists = false;

        if (hr.HasMember("fq_name")) {
            const contrail_rapidjson::Value& fq_name = hr["fq_name"];
            contrail_rapidjson::SizeType sz = fq_name.Size();
            name = fq_name[sz-1].GetString();
            LOG(DEBUG, "NAME got from fq_name: " << name);
        }
        if (hr.HasMember("structured_syslog_hostaddr")) {
            hostaddr = hr["structured_syslog_hostaddr"].GetString();
        }
        if (hr.HasMember("structured_syslog_tenant")) {
            tenant = hr["structured_syslog_tenant"].GetString();
        }
        if (hr.HasMember("structured_syslog_location")) {
            location = hr["structured_syslog_location"].GetString();
        }
        if (hr.HasMember("structured_syslog_device")) {
            device = hr["structured_syslog_device"].GetString();
        }
        if (hr.HasMember("structured_syslog_hostname_tags")) {
            tags = hr["structured_syslog_hostname_tags"].GetString();
        }
        if (hr.HasMember("structured_syslog_linkmap")) {
            const contrail_rapidjson::Value& linkmap_fields = hr["structured_syslog_linkmap"];
            const contrail_rapidjson::Value& links_array = linkmap_fields["links"];
            assert(links_array.IsArray());
            for (contrail_rapidjson::SizeType i = 0; i < links_array.Size(); i++) {
                std::string underlay = links_array[i]["underlay"].GetString();
                std::string link_type = links_array[i]["link_type"].GetString() ;
                std::string traffic_destination = links_array[i]["traffic_destination"].GetString() ; 
                std::string link_metadata = links_array[i]["metadata"].GetString() ;
                std::string overlay_link_data = underlay + "@" + link_type + "@" + traffic_destination + "@" + link_metadata ;
                linkmap.insert(std::make_pair(links_array[i]["overlay"].GetString(),
                overlay_link_data));
                LOG(DEBUG, "Adding HostnameRecord: " << name << " linkmap: "
                << links_array[i]["overlay"].GetString() << " : "
                << overlay_link_data);
            }
        }
        if (hr.HasMember("structured_syslog_lan_segment_list")) {
            const contrail_rapidjson::Value& LANSegmentList_fields = hr["structured_syslog_lan_segment_list"];
            const contrail_rapidjson::Value& LANSegmentList_array = LANSegmentList_fields["LANSegmentList"];
            assert(LANSegmentList_array.IsArray());
            for (Chr_t::iterator it = hostname_records_.begin();it != hostname_records_.end(); it++){
                    if (location == (it->second->location())) {
                        LOG(DEBUG,"location already exists in hostname_records !!");
                        location_exists = true;
                        break;
                    }
            }
            if (location_exists) {
                LOG(DEBUG, "Refresh LAN MAP for location : "<<location);
                RefreshNetworksMap(location);
            }
            for (contrail_rapidjson::SizeType i = 0; i < LANSegmentList_array.Size(); i++) {
                std::string vpn = LANSegmentList_array[i]["vpn"].GetString();
                std::string network_ranges = LANSegmentList_array[i]["network_ranges"].GetString();
                LOG(DEBUG, "Adding networks map with VPN: " << LANSegmentList_array[i]["vpn"].GetString()
                 << " LANSegmentList: " << LANSegmentList_array[i]["network_ranges"].GetString());

                std::vector<std::string> network_range_list = split_into_vector(network_ranges,',');
                for (std::vector<std::string>::iterator iter = network_range_list.begin();
                    iter != network_range_list.end(); iter++){
                    std::vector<std::string> ip_and_subnet = split_into_vector(*iter,'/');
                    std::string network_key = tenant + "::" + vpn;
                    AddNetwork (network_key, ip_and_subnet[0], ip_and_subnet[1], location);
                }
            }
        }
        if (add_update) {
            LOG(DEBUG, "Adding HostnameRecord: " << name);
            AddHostnameRecord(name, hostaddr, tenant,
                                 location, device, tags, linkmap);
        } else {
            Chr_t::iterator cit = hostname_records_.find(name);
            if (cit != hostname_records_.end()) {
                LOG(DEBUG, "Erasing LAN MAP for location : "<< cit->second->location());
                if (!cit->second->location().empty()){
                   RefreshNetworksMap(location);
                }
                LOG(DEBUG, "Erasing HostnameRecord: " << cit->second->name());
                hostname_records_.erase(cit);
            }
        }
        return;
    }
}

void
StructuredSyslogConfig::TenantRecordsHandler(const contrail_rapidjson::Document &jdoc,
                                               bool add_update) {
    if (jdoc.IsObject() && jdoc.HasMember("structured_syslog_tenant_record")) {
        const contrail_rapidjson::Value& hr = jdoc["structured_syslog_tenant_record"];
        std::string name, tenantaddr, tenant, tags;
        std::map< std::string, std::string > dscpmap_ipv4;
        std::map< std::string, std::string > dscpmap_ipv6;

        if (hr.HasMember("fq_name")) {
            const contrail_rapidjson::Value& fq_name = hr["fq_name"];
            contrail_rapidjson::SizeType sz = fq_name.Size();
            name = fq_name[sz-1].GetString();
            LOG(DEBUG, "NAME got from fq_name: " << name);
        }
        if (hr.HasMember("structured_syslog_tenantaddr")) {
            tenantaddr = hr["structured_syslog_tenantaddr"].GetString();
        }
        if (hr.HasMember("structured_syslog_tenant")) {
            tenant = hr["structured_syslog_tenant"].GetString();
        }
        if (hr.HasMember("structured_syslog_tenant_tags")) {
            tags = hr["structured_syslog_tenant_tags"].GetString();
        }
        if (hr.HasMember("structured_syslog_dscpmap")) {
            const contrail_rapidjson::Value& dscpmap_fields = hr["structured_syslog_dscpmap"];
            const contrail_rapidjson::Value& dscpmapipv4_array = dscpmap_fields["dscpListIPv4"];
            const contrail_rapidjson::Value& dscpmapipv6_array = dscpmap_fields["dscpListIPv6"];
            assert(dscpmapipv6_array.IsArray());
            assert(dscpmapipv4_array.IsArray());
            for (contrail_rapidjson::SizeType i = 0; i < dscpmapipv4_array.Size(); i++) {
                dscpmap_ipv4.insert(std::make_pair<std::string,
                std::string >(dscpmapipv4_array[i]["dscp_value"].GetString(),
                dscpmapipv4_array[i]["alias_code"].GetString()));
                LOG(DEBUG, "Adding TenantRecord: " << name << " dscpmap ipv4: "
                << dscpmapipv4_array[i]["dscp_value"].GetString() << " : "
                << dscpmapipv4_array[i]["alias_code"].GetString());
            }
            for (contrail_rapidjson::SizeType i = 0; i < dscpmapipv6_array.Size(); i++) {
                dscpmap_ipv6.insert(std::make_pair<std::string,
                std::string >(dscpmapipv6_array[i]["dscp_value"].GetString(),
                dscpmapipv6_array[i]["alias_code"].GetString()));
                LOG(DEBUG, "Adding TenantRecord: " << name << " dscpmap ipv6: "
                << dscpmapipv6_array[i]["dscp_value"].GetString() << " : "
                << dscpmapipv6_array[i]["alias_code"].GetString());
            }
        }
        if (add_update) {
            LOG(DEBUG, "Adding TenantRecord: " << name);
            AddTenantRecord(name, tenantaddr, tenant,
                                tags, dscpmap_ipv4, dscpmap_ipv6);
        } else {
            Ctr_t::iterator cit = tenant_records_.find(name);
            if (cit != tenant_records_.end()) {
                LOG(DEBUG, "Erasing TenantRecord: " << cit->second->name());
                tenant_records_.erase(cit);
            }
        }
        return;
    }
}


void
StructuredSyslogConfig::ApplicationRecordsHandler(const contrail_rapidjson::Document &jdoc,
                                                  bool add_update) {
    if (jdoc.IsObject() && jdoc.HasMember("structured_syslog_application_record")) {
        const contrail_rapidjson::Value& ar = jdoc["structured_syslog_application_record"];
        std::string name, app_category, app_subcategory,
                    app_groups, app_risk, app_service_tags;

        if (ar.HasMember("fq_name")) {
            const contrail_rapidjson::Value& fq_name = ar["fq_name"];
            contrail_rapidjson::SizeType sz = fq_name.Size();
            name = fq_name[sz-1].GetString();
            LOG(DEBUG, "NAME got from fq_name: " << name);
        }

        if (ar.HasMember("structured_syslog_app_category")) {
            app_category = ar["structured_syslog_app_category"].GetString();
        }
        if (ar.HasMember("structured_syslog_app_subcategory")) {
            app_subcategory = ar["structured_syslog_app_subcategory"].GetString();
        }
        if (ar.HasMember("structured_syslog_app_groups")) {
            app_groups = ar["structured_syslog_app_groups"].GetString();
        }
        if (ar.HasMember("structured_syslog_app_risk")) {
            app_risk = ar["structured_syslog_app_risk"].GetString();
        }
        if (ar.HasMember("structured_syslog_app_service_tags")) {
            app_service_tags = ar["structured_syslog_app_service_tags"].GetString();
        }

        const contrail_rapidjson::Value& fq_name = ar["fq_name"];
        std::string tenant_name = fq_name[1].GetString();
        if (tenant_name.compare("default-global-analytics-config") == 0) {
            if (add_update) {
                LOG(DEBUG, "Adding ApplicationRecord: " << name);
                AddApplicationRecord(name, app_category, app_subcategory,
                                        app_groups, app_risk, app_service_tags);
            } else {
                Car_t::iterator cit = application_records_.find(name);
                if (cit != application_records_.end()) {
                    LOG(DEBUG, "Erasing ApplicationRecord: " << cit->second->name());
                    application_records_.erase(cit);
                }
           }
        }
        else {
            std::string apprec_name;
            apprec_name =  tenant_name + '/' + name;
            if (add_update) {
                LOG(DEBUG, "Adding TenantApplicationRecord: " << apprec_name);
                AddTenantApplicationRecord(apprec_name, app_category, app_subcategory,
                                        app_groups, app_risk, app_service_tags);
            } else {
                Ctar_t::iterator ctit = tenant_application_records_.find(apprec_name);
                if (ctit != tenant_application_records_.end()) {
                    LOG(DEBUG, "Erasing TenantApplicationRecord: " << ctit->second->name());
                    tenant_application_records_.erase(ctit);
                }
            }
        }

        return;
    }
}

void
StructuredSyslogConfig::MessageConfigsHandler(const contrail_rapidjson::Document &jdoc,
                                              bool add_update) {
    if (jdoc.IsObject() && jdoc.HasMember("structured_syslog_message")) {
        const contrail_rapidjson::Value& hr = jdoc["structured_syslog_message"];
        std::vector< std::string > ints;
        std::vector< std::string > tags;
        std::string name, forward;
        bool process_and_store = false;
        bool process_and_summarize = false;
        bool process_and_summarize_user = false;

        if (hr.HasMember("fq_name")) {
            const contrail_rapidjson::Value& fq_name = hr["fq_name"];
            contrail_rapidjson::SizeType sz = fq_name.Size();
            name = fq_name[sz-1].GetString();
            LOG(DEBUG, "NAME got from fq_name: " << name);
        }

        if (hr.HasMember("structured_syslog_message_tagged_fields")) {
            const contrail_rapidjson::Value& tagged_fields = hr["structured_syslog_message_tagged_fields"];
            const contrail_rapidjson::Value& tag_array = tagged_fields["field_names"];
            assert(tag_array.IsArray());
            for (contrail_rapidjson::SizeType i = 0; i < tag_array.Size(); i++)
                tags.push_back(tag_array[i].GetString());
        }
        if (hr.HasMember("structured_syslog_message_integer_fields")) {
            const contrail_rapidjson::Value& integer_fields = hr["structured_syslog_message_integer_fields"];
            const contrail_rapidjson::Value& int_array = integer_fields["field_names"];
            assert(int_array.IsArray());
            for (contrail_rapidjson::SizeType i = 0; i < int_array.Size(); i++)
                ints.push_back(int_array[i].GetString());
        }
        if (hr.HasMember("structured_syslog_message_forward")) {
            forward = hr["structured_syslog_message_forward"].GetString();
        }
        if (hr.HasMember("structured_syslog_message_process_and_store")) {
            process_and_store = hr["structured_syslog_message_process_and_store"].GetBool();
        }
        if (hr.HasMember("structured_syslog_message_process_and_summarize")) {
            process_and_summarize = hr["structured_syslog_message_process_and_summarize"].GetBool();
        }
        if (hr.HasMember("structured_syslog_message_process_and_summarize_user")) {
            process_and_summarize_user = hr["structured_syslog_message_process_and_summarize_user"].GetBool();
        }
        if (add_update) {
            LOG(DEBUG, "Adding MessageConfig: " << name);
            AddMessageConfig(name, tags, ints, process_and_store, forward, process_and_summarize, process_and_summarize_user);
        } else {
            Cmc_t::iterator cit = message_configs_.find(name);
            if (cit != message_configs_.end()) {
                LOG(DEBUG, "Erasing MessageConfig: " << cit->second->name());
                message_configs_.erase(cit);
            }
        }
        return;
    }

}

void
StructuredSyslogConfig::SlaProfileRecordsHandler(const contrail_rapidjson::Document &jdoc,
                                                 bool add_update) {
    if (jdoc.IsObject() && jdoc.HasMember("structured_syslog_sla_profile")) {
        const contrail_rapidjson::Value& slar = jdoc["structured_syslog_sla_profile"];
        std::string name, sla_params, tenant_name, slarec_name;

        if (slar.HasMember("fq_name")) {
            const contrail_rapidjson::Value& fq_name = slar["fq_name"];
            contrail_rapidjson::SizeType sz = fq_name.Size();
            name = fq_name[sz-1].GetString();
            tenant_name = fq_name[1].GetString();
            slarec_name =  tenant_name + '/' + name;
            LOG(DEBUG, "NAME got from fq_name: " << name);
        }
        if (slar.HasMember("structured_syslog_sla_params")) {
            sla_params = slar["structured_syslog_sla_params"].GetString();
        }
        if (add_update) {
            LOG(DEBUG, "Adding SlaProfileRecord: " << slarec_name);
            AddSlaProfileRecord(slarec_name, sla_params);
        } else {
            Csr_t::iterator cit = sla_profile_records_.find(slarec_name);
            if (cit != sla_profile_records_.end()) {
                LOG(DEBUG, "Erasing SlaProfileRecord: " << cit->second->name());
                sla_profile_records_.erase(cit);
            }
        }
        return;
    }
}

void
StructuredSyslogConfig::ReceiveConfig(const contrail_rapidjson::Document &jdoc, bool add_change) {
    HostnameRecordsHandler(jdoc, add_change);
    TenantRecordsHandler(jdoc, add_change);
    ApplicationRecordsHandler(jdoc, add_change);
    MessageConfigsHandler(jdoc, add_change);
    SlaProfileRecordsHandler(jdoc, add_change);
}

boost::shared_ptr<HostnameRecord>
StructuredSyslogConfig::GetHostnameRecord(const std::string &name) {
    Chr_t::iterator it = hostname_records_.find(name);
    if (it  != hostname_records_.end()) {
        return it->second;
    }
    return boost::shared_ptr<HostnameRecord>();
}

void
StructuredSyslogConfig::AddHostnameRecord(const std::string &name,
        const std::string &hostaddr, const std::string &tenant,
        const std::string &location, const std::string &device,
        const std::string &tags, const std::map< std::string, std::string > &linkmap) {
    Chr_t::iterator it = hostname_records_.find(name);
    if (it  != hostname_records_.end()) {
        it->second->Refresh(name, hostaddr, tenant, location,
                           device, tags, linkmap);
    } else {
        boost::shared_ptr<HostnameRecord> c(new HostnameRecord(
                    name, hostaddr, tenant, location, device, tags, linkmap));
        hostname_records_.insert(std::make_pair(name, c));
    }
}

boost::shared_ptr<TenantRecord>
StructuredSyslogConfig::GetTenantRecord(const std::string &name) {
    Ctr_t::iterator it = tenant_records_.find(name);
    if (it  != tenant_records_.end()) {
        return it->second;
    }
    return boost::shared_ptr<TenantRecord>();
}

void
StructuredSyslogConfig::AddTenantRecord(const std::string &name,
        const std::string &tenantaddr, const std::string &tenant,
        const std::string &tags, const std::map< std::string, std::string > &dscpmap_ipv4,
        const std::map< std::string, std::string > &dscpmap_ipv6) {
    Ctr_t::iterator it = tenant_records_.find(name);
    if (it  != tenant_records_.end()) {
        it->second->Refresh(name, tenantaddr, tenant, tags, dscpmap_ipv4, dscpmap_ipv6);
    } else {
        boost::shared_ptr<TenantRecord> c(new TenantRecord(
                    name, tenantaddr, tenant, tags, dscpmap_ipv4, dscpmap_ipv6));
        tenant_records_.insert(std::make_pair(name, c));
    }
}

boost::shared_ptr<ApplicationRecord>
StructuredSyslogConfig::GetApplicationRecord(const std::string &name) {
    Car_t::iterator it = application_records_.find(name);
    if (it  != application_records_.end()) {
        return it->second;
    }
    return boost::shared_ptr<ApplicationRecord>();
}

void
StructuredSyslogConfig::AddApplicationRecord(const std::string &name,
        const std::string &app_category, const std::string &app_subcategory,
        const std::string &app_groups, const std::string &app_risk,
        const std::string &app_service_tags) {
    Car_t::iterator it = application_records_.find(name);
    if (it  != application_records_.end()) {
        it->second->Refresh(name, app_category, app_subcategory, app_groups,
                           app_risk, app_service_tags);
    } else {
        boost::shared_ptr<ApplicationRecord> c(new ApplicationRecord(
                    name, app_category, app_subcategory, app_groups,
                    app_risk, app_service_tags));
        application_records_.insert(std::make_pair(name, c));
    }
}

boost::shared_ptr<TenantApplicationRecord>
StructuredSyslogConfig::GetTenantApplicationRecord(const std::string &name) {
    Ctar_t::iterator it = tenant_application_records_.find(name);
    if (it  != tenant_application_records_.end()) {
        return it->second;
    }
    return boost::shared_ptr<TenantApplicationRecord>();
}

void
StructuredSyslogConfig::AddTenantApplicationRecord(const std::string &name,
        const std::string &tenant_app_category, const std::string &tenant_app_subcategory,
        const std::string &tenant_app_groups, const std::string &tenant_app_risk,
        const std::string &tenant_app_service_tags) {
    Ctar_t::iterator it = tenant_application_records_.find(name);
    if (it  != tenant_application_records_.end()) {
        it->second->Refresh(name, tenant_app_category, tenant_app_subcategory,
                        tenant_app_groups, tenant_app_risk, tenant_app_service_tags);
    } else {
        boost::shared_ptr<TenantApplicationRecord> c(new TenantApplicationRecord(
                    name, tenant_app_category, tenant_app_subcategory,
                    tenant_app_groups, tenant_app_risk, tenant_app_service_tags));
        tenant_application_records_.insert(std::make_pair(name, c));
    }
}

boost::shared_ptr<SlaProfileRecord>
StructuredSyslogConfig::GetSlaProfileRecord(const std::string &name) {
    Csr_t::iterator it = sla_profile_records_.find(name);
    if (it  != sla_profile_records_.end()) {
        return it->second;
    }
    return boost::shared_ptr<SlaProfileRecord>();
}

void
StructuredSyslogConfig::AddSlaProfileRecord(const std::string &name,
        const std::string &sla_params) {
    Csr_t::iterator it = sla_profile_records_.find(name);
    if (it  != sla_profile_records_.end()) {
        it->second->Refresh(name, sla_params);
    } else {
        boost::shared_ptr<SlaProfileRecord> c(new SlaProfileRecord(
                    name, sla_params));
        sla_profile_records_.insert(std::make_pair(name, c));
    }
}

boost::shared_ptr<MessageConfig>
StructuredSyslogConfig::GetMessageConfig(const std::string &name) {
    Cmc_t::iterator it = message_configs_.find(name);
    if (it  != message_configs_.end()) {
        /* exact match */
        return it->second;
    }
    /* no exact match, look for match based on regex */
    Cmc_t::iterator cit = message_configs_.begin();
    Cmc_t::iterator end = message_configs_.end();
    Cmc_t::iterator match = end;
    while (cit != end) {
        regex pattern;
        Cmc_t::iterator dit = cit++;
        boost::match_results<std::string::const_iterator> what;
        boost::match_flag_type flags = boost::match_default;
        std::string::const_iterator name_start = name.begin(), name_end = name.end();
        try {
            pattern = regex(dit->second->name());
        }
        catch (regex_error &e) {
            LOG(DEBUG, "skipping invalid regex pattern: " << dit->second->name());
            continue;
        }
        if(regex_search(name_start, name_end, what, pattern, flags)) {
            if ((match == end) || (match->second->name().length() < dit->second->name().length())) {
                match = dit;
            }
        }
    }
    if (match != end) {
        return match->second;
    }
    /* no match */
    return boost::shared_ptr<MessageConfig>();
}

void
StructuredSyslogConfig::AddMessageConfig(const std::string &name,
        const std::vector< std::string > &tags, const std::vector< std::string > &ints,
        bool process_and_store, const std::string &forward_action, bool process_and_summarize, 
        bool process_and_summarize_user) {
    bool forward = false, process_before_forward = false;
    if (forward_action == "forward-unprocessed") {
        forward = true;
    }
    if (forward_action == "forward-processed") {
        forward = true;
        process_before_forward = true;
    }
    Cmc_t::iterator it = message_configs_.find(name);
    if (it  != message_configs_.end()) {
        it->second->Refresh(name, tags, ints, process_and_store, forward, process_before_forward, 
                            process_and_summarize, process_and_summarize_user);
    } else {
        boost::shared_ptr<MessageConfig> c(new MessageConfig(
                    name, tags, ints, process_and_store, forward, process_before_forward, 
		    process_and_summarize, process_and_summarize_user));
        message_configs_.insert(std::make_pair(name, c));
    }
}
