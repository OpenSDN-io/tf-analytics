/*
 * Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
 */

/*
 * This file will not contain actual query processing code but instead only
 * the code to 
 * a) Interact with external interfaces like REDIS etc.
 * b) Parse JSON strings passed to populate the query structures
 */

#include "rapidjson/document.h"
#include "base/logging.h"
#include "query.h"
#include <boost/assign/list_of.hpp>
#include <boost/foreach.hpp>
#include <cerrno>
#include <contrail-collector/vizd_table_desc.h>
#include "stats_select.h"
#include "stats_query.h"
#include "base/regex.h"
#include "base/connection_info.h"
#include "utils.h"
#include <database/cassandra/cql/cql_if.h>
#include <boost/make_shared.hpp>
#include "qe_sandesh.h"
#include <algorithm>

using std::map;
using std::string;
using std::vector;
using boost::assign::map_list_of;
using boost::system::error_code;
using contrail::regex;
using contrail::regex_match;
using contrail::regex_search;
using process::ConnectionState;
using process::ConnectionType;
using process::ConnectionStatus;

int QueryEngine::max_slice_ = 100;

bool oldDataExists;

typedef  std::vector< std::pair<std::string, std::string> > spair_vector;
static spair_vector query_string_to_column_name(0);

std::string get_column_name(std::string query_string)
{
    spair_vector::iterator iter; 

    for (iter = query_string_to_column_name.begin();
            iter != query_string_to_column_name.end();
            iter++)
    {
        if (iter->first == query_string)
            return iter->second;
    }

    return query_string;
}

std::string get_query_string(std::string column_name)
{
    spair_vector::iterator iter; 

    for (iter = query_string_to_column_name.begin();
            iter != query_string_to_column_name.end();
            iter++)
    {
        if (iter->second == column_name)
            return iter->first;
    }

    return column_name;
}

QueryResultMetaData::~QueryResultMetaData() {
}

PostProcessingQuery::PostProcessingQuery(
    const std::map<std::string, std::string>& json_api_data,
    QueryUnit *main_query) :  QueryUnit(main_query, main_query), 
        sorted(false), limit(0) {
    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
    std::map<std::string, std::string>::const_iterator iter;

    QE_TRACE(DEBUG, __func__ );

    json_string_ = "";

    for (iter = json_api_data.begin(); iter != json_api_data.end(); iter++)
    {
        if (iter->first == QUERY_SORT_OP)
        {
            sorted = true;
            int tmp;
            std::istringstream(iter->second) >> tmp; 
            sorting_type = (sort_op)tmp;
            m_query->merge_needed = true;
            QE_TRACE(DEBUG, "sorting_type :" << sorting_type);
            json_string_ += iter->second;
            json_string_ += " ";
        }
        
        if (iter->first == QUERY_LIMIT)
        {
            std::istringstream(iter->second) >> limit;
            m_query->merge_needed = true;
            QE_TRACE(DEBUG, "limit :"<< limit);
            json_string_ += iter->second;
            json_string_ += " ";
        }

        if (iter->first == QUERY_SORT_FIELDS)
        {
            contrail_rapidjson::Document d;
            std::string json_string = "{ \"sort_fields\" : " + 
                iter->second + " }";
            json_string_ += json_string;
            json_string_ += " ";

            d.Parse<0>(const_cast<char *>(json_string.c_str()));
            const contrail_rapidjson::Value& json_sort_fields =
                d["sort_fields"]; 
            QE_PARSE_ERROR(json_sort_fields.IsArray());
            QE_TRACE(DEBUG, "# of sort fields:"<< json_sort_fields.Size());
            for (contrail_rapidjson::SizeType i = 0; i<json_sort_fields.Size(); i++) 
            {
                QE_PARSE_ERROR(json_sort_fields[i].IsString());
                std::string sort_str(json_sort_fields[i].GetString());
                QE_TRACE(DEBUG, "sort field:" << sort_str);
                std::string datatype(m_query->get_column_field_datatype(sort_str));
                if (m_query->is_stat_table_query(m_query->table()) &&
                       (m_query->stats().is_stat_table_static())) {
                    // This is a static StatTable. We can check the schema
                    std::string sfield;
                    
                    // If this is an agg field, check underlying data type
                    if (StatsQuery::ParseAgg(sort_str, sfield) !=
                            QEOpServerProxy::INVALID) {
                        std::string dtype2(m_query->get_column_field_datatype(sfield));
                        QE_INVALIDARG_ERROR(dtype2 != std::string(""));
                    } else {
                        QE_INVALIDARG_ERROR(datatype != std::string(""));
                    }
                }
                QE_INVALIDARG_ERROR(
                    m_query->is_valid_sort_field(sort_str) != false);
                sort_field_t sort_field(get_column_name(sort_str), datatype);
                sort_fields.push_back(sort_field);
            }
        }

        /*
         * old filter style is just list of expr ANDed
         * new filter are list of ANDs over OR
         * both modes are supported with the below code
         */
        if (iter->first == QUERY_FILTER) {
            contrail_rapidjson::Document d;
            std::string json_string = "{ \"filter\" : " + 
                iter->second + " }";
            json_string_ += json_string;
            json_string_ += " ";

            d.Parse<0>(const_cast<char *>(json_string.c_str()));
            const contrail_rapidjson::Value& json_filters =
                d["filter"]; 
            QE_PARSE_ERROR(json_filters.IsArray());
            QE_TRACE(DEBUG, "# of filters:"<< json_filters.Size());
            bool single_list = false;
            if (json_filters.Size()) {
                contrail_rapidjson::SizeType zeroth = 0;
                const contrail_rapidjson::Value& json_filters_0 = json_filters[zeroth];
                if (!json_filters_0.IsArray()) {
                    single_list = true;
                }
            }

            if (single_list) {
                //parse the old format 
                std::vector<filter_match_t> filter_and;
                for (contrail_rapidjson::SizeType j = 0; j<json_filters.Size(); j++) 
                  {
                    filter_match_t filter;
                    QE_PARSE_ERROR((json_filters[j].HasMember(WHERE_MATCH_NAME)
                        && json_filters[j].HasMember(WHERE_MATCH_VALUE)
                        && json_filters[j].HasMember(WHERE_MATCH_OP)));
                    const contrail_rapidjson::Value& name_value = 
                        json_filters[j][WHERE_MATCH_NAME];
                    const contrail_rapidjson::Value&  value_value = 
                        json_filters[j][WHERE_MATCH_VALUE];
                    const contrail_rapidjson::Value& op_value = 
                        json_filters[j][WHERE_MATCH_OP];

                    // do some validation checks
                    QE_INVALIDARG_ERROR(name_value.IsString());
                    QE_INVALIDARG_ERROR
                        ((value_value.IsString() || value_value.IsNumber() ||
                          value_value.IsDouble()));
                    QE_INVALIDARG_ERROR(op_value.IsNumber());

                    filter.name = name_value.GetString();
                    filter.op = (match_op)op_value.GetInt();

                    // extract value after type conversion
                      {
                        if (value_value.IsString())
                          {
                            filter.value = value_value.GetString();
                          } else if (value_value.IsInt()){
                              int int_value;
                              std::ostringstream convert;
                              int_value = value_value.GetInt();
                              convert << int_value;
                              filter.value = convert.str();
                          } else if (value_value.IsUint()) {
                              uint32_t uint_value;
                              std::ostringstream convert;
                              uint_value = value_value.GetUint();
                              convert << uint_value;
                              filter.value = convert.str();
                          } else if (value_value.IsDouble()) {
                              double dbl_value;
                              std::ostringstream convert;
                              dbl_value = value_value.GetDouble();
                              convert << dbl_value;
                              filter.value = convert.str();
                          }
                      }

                    if (filter.op == REGEX_MATCH)
                      {
                        // compile regex beforehand
                        filter.match_e = regex(filter.value);
                      }

                    filter_and.push_back(filter);
                  }
                filter_list.push_back(filter_and);
            } else {
                //new OR of ANDs
                for (contrail_rapidjson::SizeType j = 0; j<json_filters.Size(); j++) {
                    std::vector<filter_match_t> filter_and;
                    const contrail_rapidjson::Value& json_filter_and = json_filters[j];
                    QE_PARSE_ERROR(json_filter_and.IsArray());

                    for (contrail_rapidjson::SizeType k = 0; k<json_filter_and.Size(); k++) {
                        filter_match_t filter;
                        QE_PARSE_ERROR((
                            json_filter_and[k].HasMember(WHERE_MATCH_NAME)
                            && json_filter_and[k].HasMember(WHERE_MATCH_VALUE)
                            && json_filter_and[k].HasMember(WHERE_MATCH_OP)));
                        const contrail_rapidjson::Value& name_value = 
                            json_filter_and[k][WHERE_MATCH_NAME];
                        const contrail_rapidjson::Value&  value_value = 
                            json_filter_and[k][WHERE_MATCH_VALUE];
                        const contrail_rapidjson::Value& op_value = 
                            json_filter_and[k][WHERE_MATCH_OP];

                        // do some validation checks
                        QE_INVALIDARG_ERROR(name_value.IsString());
                        QE_INVALIDARG_ERROR
                            ((value_value.IsString() || value_value.IsNumber()));
                        QE_INVALIDARG_ERROR(op_value.IsNumber());

                        filter.name = name_value.GetString();
                        filter.op = (match_op)op_value.GetInt();

                        // extract value after type conversion
                        if (value_value.IsString()) {
                            filter.value = value_value.GetString();
                        } else if (value_value.IsInt()) {
                            int int_value;
                            std::ostringstream convert;
                            int_value = value_value.GetInt();
                            convert << int_value;
                            filter.value = convert.str();
                        } else if (value_value.IsUint()) {
                            uint32_t uint_value;
                            std::ostringstream convert;
                            uint_value = value_value.GetUint();
                            convert << uint_value;
                            filter.value = convert.str();
                        }

                        if (filter.op == REGEX_MATCH) {
                            // compile regex beforehand
                            filter.match_e = regex(filter.value);
                        }

                        filter_and.push_back(filter);
                    }
                    filter_list.push_back(filter_and);
                }
            }
        }
    }

    if (!m_query->wherequery_->filter_list_.empty()) {
        if (filter_list.empty()) {
            filter_list = m_query->wherequery_->filter_list_;
        } else {
            BOOST_FOREACH(std::vector<filter_match_t> &filter_and, filter_list) {
                BOOST_FOREACH(const std::vector<filter_match_t> &where_filter_and,
                        m_query->wherequery_->filter_list_) {
                    filter_and.insert(filter_and.end(), where_filter_and.begin(),
                        where_filter_and.end());
                }
            }
        }
    }

    // add filter to filter query engine logs if requested
    if (((AnalyticsQuery *)main_query)->filter_qe_logs &&
        ((AnalyticsQuery *)main_query)->is_message_table_query()) {
        QE_TRACE(DEBUG,  " Adding filter for QE logs");
        filter_match_t filter;
        filter.name = g_viz_constants.MODULE;
        filter.value = 
            ((AnalyticsQuery *)main_query)->sandesh_moduleid;
        filter.op = NOT_EQUAL;
        filter.ignore_col_absence = true;
        if (!filter_list.size()) {
            std::vector<filter_match_t> filter_and;
            filter_and.push_back(filter);
            filter_list.push_back(filter_and);
        } else {
            for (unsigned int i = 0; i < filter_list.size(); i++) {
                filter_list[i].push_back(filter);
            }
        }
    }

    // If the user has specified the sorting field and not the sorting order,
    // then sort the result in ascending order.
    if (sort_fields.size() && sorted == false) {
        sorted = true;
        sorting_type = ASCENDING; 
    }
}

bool AnalyticsQuery::merge_processing(
    const QEOpServerProxy::BufferT& input, 
    QEOpServerProxy::BufferT& output) {

    if (status_details != 0)
    {
        QE_TRACE(DEBUG, 
             "No need to process query, as there were errors previously");
        return false;
    }

    // Have the result ready and processing is done
    status_details = 0;
    return postprocess_->merge_processing(input, output);
}

bool AnalyticsQuery::final_merge_processing(
const std::vector<boost::shared_ptr<QEOpServerProxy::BufferT> >& inputs,
    QEOpServerProxy::BufferT& output) {

    if (status_details != 0)
    {
        QE_TRACE(DEBUG, 
             "No need to process query, as there were errors previously");
        return false;
    }

    // Have the result ready and processing is done
    status_details = 0;
    return postprocess_->final_merge_processing(inputs, output);
}

// this is to get parallelization details once the query is parsed
void AnalyticsQuery::get_query_details(bool& is_merge_needed, bool& is_map_output,
        std::vector<uint64_t>& chunk_sizes,
        std::string& where, uint32_t& wterms,
        std::string& select,
        std::string& post,
        uint64_t& time_period,
        int& parse_status)
{
    QE_TRACE(DEBUG, "time_slice is " << time_slice);
    if (status_details == 0)
    {
        for (uint64_t chunk_start = original_from_time; 
                chunk_start < original_end_time; chunk_start += time_slice)
        {
            if ((chunk_start+time_slice) <= original_end_time) {
                chunk_sizes.push_back(time_slice);
            } else {
                chunk_sizes.push_back((original_end_time - chunk_start));
            }
        }
    } else {
        chunk_sizes.push_back(0); // just return some dummy value
    }

    time_period = (end_time_ - from_time_) / 1000000;

    parse_status = status_details;
    if (parse_status != 0) return;
    
    if (is_stat_table_query(table_)
        || is_session_query(table_)
        || is_flow_query(table_)) {
        is_merge_needed = selectquery_->stats_->IsMergeNeeded();
    } else {
        is_merge_needed = merge_needed;
    }

    where = wherequery_->json_string_;
    wterms = wherequery_->wterms_;
    select = selectquery_->json_string_;
    post = postprocess_->json_string_;
    is_map_output = is_stat_table_query(table_)
                        || is_session_query(table_)
                        || is_flow_query(table_);
}

bool AnalyticsQuery::can_parallelize_query() {
    parallelize_query_ = true;
    if (table_ == g_viz_constants.OBJECT_VALUE_TABLE) {
        parallelize_query_ = false;
    }
    return parallelize_query_;
}

/* parse the stat name and attribute which can be used
 * to collect stats. Table name is of the format,
 * StatTable.TableName.AttrName
 * The functions sets the stat_name_attr member, which is later
 * used to collect stats information
 */
void AnalyticsQuery::ParseStatName(std::string& stat_table_name) {
     string stat_table("StatTable.");
     stat_name_attr = stat_table_name.substr(stat_table.length());
     std::replace(stat_name_attr.begin(), stat_name_attr.end(), '.', ':');
}

void AnalyticsQuery::Init(const std::string& qid,
    const std::map<std::string, std::string>& json_api_data,
    int32_t or_number)
{
    std::map<std::string, std::string>::const_iterator iter;

    QE_TRACE(DEBUG, __func__);
    
    // populate fields 
    query_id = qid;

    sandesh_moduleid = 
        g_vns_constants.ModuleNames.find(Module::QUERY_ENGINE)->second;

    {
        std::stringstream json_string; json_string << " { ";
        for (std::map<std::string, 
                std::string>::iterator it = json_api_data_.begin();
                it != json_api_data_.end(); it++) {
            json_string << 
                ((it != json_api_data_.begin())? " , " : "") <<
                it->first << ": " << it->second;
        }
        json_string << " } ";
        QE_LOG_GLOBAL(DEBUG, "json query is: " << json_string.str());
    }

    // parse JSON query
    // FROM field
    {
        iter = json_api_data.find(QUERY_TABLE);
        QE_PARSE_ERROR(iter != json_api_data.end());

        //strip " from the passed string
        table_ = iter->second.substr(1, iter->second.size()-2);

        // boost::to_upper(table);
        QE_TRACE(DEBUG,  " table is " << table_);
        if (is_stat_table_query(table_)) {
            stats_.reset(new StatsQuery(table_));
            ParseStatName(table_);
        }
    }

    uint64_t ttl;
    uint64_t min_start_time = UTCTimestampUsec();
    uint64_t max_end_time = min_start_time;

    if (is_stat_table_query(table_)) {
        ttl = ttlmap_.find(TtlType::STATSDATA_TTL)->second;
    } else if (is_flow_query(table_) || is_session_query(table_)) {
        ttl = ttlmap_.find(TtlType::FLOWDATA_TTL)->second;
    } else if (is_object_table_query(table_)) {
        ttl = ttlmap_.find(TtlType::CONFIGAUDIT_TTL)->second;
    } else {
        ttl = ttlmap_.find(TtlType::GLOBAL_TTL)->second;
    }
    min_start_time = min_start_time-ttl*60*60*1000000;

    // Start time
    {
        iter = json_api_data.find(QUERY_START_TIME);
        QE_PARSE_ERROR(iter != json_api_data.end());
        QE_PARSE_ERROR(parse_time(iter->second, &req_from_time_));
        QE_TRACE(DEBUG,  " from_time is " << req_from_time_);
        if (req_from_time_ < min_start_time) 
        {
            from_time_ = min_start_time;
            QE_TRACE(DEBUG, "updated start_time to:" << from_time_);
        } else {
            from_time_ = req_from_time_;
        }
    }

    // End time
    {
        iter = json_api_data.find(QUERY_END_TIME);
        QE_PARSE_ERROR(iter != json_api_data.end());
        QE_PARSE_ERROR(parse_time(iter->second, &req_end_time_));
        QE_TRACE(DEBUG,  " end_time is " << req_end_time_);

        if (req_end_time_ > max_end_time) {
            end_time_ = max_end_time;
            QE_TRACE(DEBUG, "updated end_time to:" << end_time_);
        } else {
            end_time_ = req_end_time_;
        }
    }

    if (is_stat_fieldnames_table_query(table_)) {
        uint64_t time_period = (end_time_ - from_time_); /* in usec */
        uint64_t cache_time = (1 << (g_viz_constants.RowTimeInBits +
                                     g_viz_constants.CacheTimeInAdditionalBits));
        if (time_period < cache_time) {
            uint64_t diff_time_usec = (cache_time - time_period);
            from_time_ = from_time_ - diff_time_usec;
            if (from_time_ < min_start_time) {
                from_time_ = min_start_time;
            }
        }
    }

    // Initialize SELECT/WHERE/Post-Processing components of query
    // for input validation

    // where processing initialization
    std::string where_json_string;
    {
        int direction = INGRESS;
        iter = json_api_data.find(QUERY_FLOW_DIR);
        if (iter != json_api_data.end()) {
            std::istringstream(iter->second) >> direction;
            QE_TRACE(DEBUG,  "set flow direction to:" << direction);
        }

        int is_si = 0;
        iter = json_api_data.find(QUERY_SESSION_IS_SI);
        if (iter != json_api_data.end()) {
            std::istringstream(iter->second) >> is_si;
            QE_TRACE(DEBUG,  "set session is_si to:" << is_si);
        }

        int session_type = 0;
        iter = json_api_data.find(QUERY_SESSION_TYPE);
        if (iter != json_api_data.end()) {
            if (iter->second == "\"client\"") {
                session_type = 1;
            } else if (iter->second == "\"server\"") {
                session_type = 0;
            } else {
                QE_INVALIDARG_ERROR(false && "session_type_invalid");
            }
            QE_TRACE(DEBUG,  "set session is_si to:" << session_type);
        }
        else if (is_session_query(table_)) {
            QE_LOG_GLOBAL(ERROR, "session_type is required for session queries");
            this->status_details = -1;
            return;
        }
        
        iter = json_api_data.find(QUERY_WHERE);
        if (iter == json_api_data.end())
        {
            QE_TRACE(DEBUG, "Where * query");
            where_json_string = std::string("");
        } else {
            where_json_string = iter->second;
        }

        QE_TRACE(DEBUG,  " Initializing Where Query");
        wherequery_ = new WhereQuery(where_json_string, session_type,
                is_si, direction, or_number, this);
        this->status_details = wherequery_->status_details;
        if (this->status_details != 0 )
        {
            QE_LOG_GLOBAL(DEBUG, "Error in WHERE parsing");
            return;
        }
    }

    // select processing initialization
    {
        QE_TRACE(DEBUG,  " Initializing Select Query");
        selectquery_ = new SelectQuery(this, json_api_data);
        this->status_details = selectquery_->status_details;
        if (this->status_details != 0 )
        {
            QE_LOG_GLOBAL(DEBUG, "Error in SELECT parsing");
            return;
        }
        /*
         * ObjectId queries are special, they are requested from Object* tables,
         * but the values are extrated from g_viz_constants.OBJECT_VALUE_TABLE
         */
        if (is_object_table_query(table_)) {
            if (selectquery_->ObjectIdQuery()) {
                object_value_key = table_;
                table_ = g_viz_constants.OBJECT_VALUE_TABLE;
            }
        }
    }

    // post processing initialization
    QE_TRACE(DEBUG,  " Initializing PostProcessing Query");
    postprocess_ = new PostProcessingQuery(json_api_data, this);
    this->status_details = postprocess_->status_details;
    if (this->status_details != 0 )
    {
        QE_LOG_GLOBAL(DEBUG, "Error in PostProcess parsing");
        return;
    }

    if (is_stat_table_query(table_)
        || is_session_query(table_)
        || is_flow_query(table_)) {
        selectquery_->stats_->SetSortOrder(postprocess_->sort_fields);
    }

    // just to take care of issues with Analytics start time 
         if (from_time_ > end_time_)
            from_time_ = end_time_ - 1; 

    // Get the right job slice for parallelization
    original_from_time = from_time_;
    original_end_time = end_time_;

    if (can_parallelize_query()) {
        uint64_t smax = pow(2,g_viz_constants.RowTimeInBits) * \
              QueryEngine::max_slice_;

        time_slice = ((end_time_ - from_time_)/total_parallel_batches) + 1;

        if (time_slice < (uint64_t)pow(2,g_viz_constants.RowTimeInBits)) {
            time_slice = pow(2,g_viz_constants.RowTimeInBits);
        }
        if (time_slice > smax) {
            time_slice = smax;
        }          
        QE_TRACE(DEBUG, "time_slice:" << time_slice << " , # of parallel "
                "batches:" << total_parallel_batches);

    } else {
        // No parallelization
        QE_LOG_GLOBAL(DEBUG, "No parallelization for this query");
        merge_needed = false;
        parallelize_query_ = false;
        time_slice = end_time_ - from_time_;
    }

    from_time_ = 
        original_from_time + time_slice*parallel_batch_num;
    end_time_ = from_time_ + time_slice;
    if (from_time_ >= original_end_time)
    {
        processing_needed = false;
    } else if (end_time_ > original_end_time) {
        end_time_ = original_end_time;
    }

    if (processing_needed)
    {
        // change it to trace later TBD
        QE_TRACE(DEBUG, "For batch:" << parallel_batch_num << " from_time:" << from_time_ << " end_time:" << end_time_ << " time slice:" << time_slice);
    } else {
        QE_TRACE(DEBUG, "No processing needed for batch:" << parallel_batch_num);
    }

}
QueryUnit::QueryUnit(QueryUnit *p_query, QueryUnit *m_query):
    parent_query(p_query), main_query(m_query), pending_subqueries(0),
    query_status(QUERY_PROCESSING_NOT_STARTED), status_details(0) 
{
    if (p_query)
        p_query->sub_queries.push_back(this);
};

QueryUnit::~QueryUnit()
{
    int num_sub_queries = sub_queries.size();
    for(int i = 0; i<num_sub_queries; i++)
        delete sub_queries[i];
}


// Get UUID from the info field
void query_result_unit_t::get_uuid(boost::uuids::uuid& u) const
{
    try {
        u = boost::get<boost::uuids::uuid>(info.at(0));
    } catch (boost::bad_get& ex) {
        QE_ASSERT(0);
    }
}

void query_result_unit_t::set_stattable_info(
        const std::string& attribstr,
        const boost::uuids::uuid& uuid) {
    info.push_back(attribstr);
    info.push_back(uuid);
}

void query_result_unit_t::get_objectid(std::string& object_id) const {
    try {
        object_id = boost::get<std::string>(info.at(1));
    } catch (boost::bad_get& ex) {
        QE_ASSERT(0);
    } catch (const std::out_of_range& oor) {
        QE_ASSERT(0);
    }
}

void  query_result_unit_t::get_stattable_info(
            std::string& attribstr,
            boost::uuids::uuid& uuid) const {

    int index = 0;

    try {
        attribstr = boost::get<std::string>(info.at(index++));
    } catch (boost::bad_get& ex) {
        QE_ASSERT(0);
    } catch (const std::out_of_range& oor) {
        QE_ASSERT(0);
    }

    try {
        uuid = boost::get<boost::uuids::uuid>(info.at(index++));
    } catch (boost::bad_get& ex) {
        QE_ASSERT(0);
    } catch (const std::out_of_range& oor) {
        QE_ASSERT(0);
    }

}

query_status_t AnalyticsQuery::process_query()
{
    if (status_details != 0)
    {
        QE_TRACE(DEBUG, 
             "No need to process query, as there were errors previously");
        return QUERY_FAILURE;
    }

    QE_TRACE(DEBUG, "Start Select Processing");
    select_start_ = UTCTimestampUsec();
    query_status = selectquery_->process_query();
    status_details = selectquery_->status_details;
    qperf_.chunk_select_time =
            static_cast<uint32_t>((UTCTimestampUsec() - select_start_)/1000);

    if (query_status != QUERY_SUCCESS)
    {
        QE_LOG(DEBUG, 
                "select processing failed with error:"<< query_status);
        return query_status;
    }
    QE_TRACE(DEBUG, "End Select Processing. row #s:" << 
            selectquery_->result_->size());
    QE_TRACE(DEBUG, "Start PostProcessing");
    postproc_start_ = UTCTimestampUsec();
    query_status = postprocess_->process_query();
    status_details = postprocess_->status_details;
    qperf_.chunk_postproc_time =
            static_cast<uint32_t>((UTCTimestampUsec() - postproc_start_)/1000);

    final_result = std::move(postprocess_->result_);
    final_mresult = std::move(postprocess_->mresult_);
    if (query_status != QUERY_SUCCESS)
    {
        QE_LOG(DEBUG, 
                "post processing failed with error:"<< query_status);
        return query_status;
    }
    QE_TRACE(DEBUG, "End PostProcessing. row #s:" << 
            final_result->size());
    return QUERY_SUCCESS;
}

AnalyticsQuery::AnalyticsQuery(const std::string& qid, std::map<std::string,
        std::string>& json_api_data,
        int or_number,
        const std::vector<query_result_unit_t> * where_info,
        const TtlMap& ttlmap,
        EventManager *evm, std::vector<std::string> cassandra_ips, 
        std::vector<int> cassandra_ports, int batch,
        int total_batches, const std::string& cassandra_user,
        const std::string& cassandra_password,
        QueryEngine* qe,
        void *handle):
        QueryUnit(NULL, this),
        filter_qe_logs(true),
        json_api_data_(json_api_data),
        where_info_(where_info),
        ttlmap_(ttlmap),
        where_start_(0),
        select_start_(0),
        postproc_start_(0),
        merge_needed(false),
        parallel_batch_num(batch),
        total_parallel_batches(total_batches),
        processing_needed(true),
        qe_(qe),
        handle_(handle),
        stats_(nullptr)
{
    assert(dbif_ != NULL);
    // Need to do this for logging/tracing with query ids
    query_id = qid;

    QE_TRACE(DEBUG, __func__);

    // Initialize database connection
    QE_TRACE(DEBUG, "Initializing database");

    boost::system::error_code ec;
    if (!dbif_->Db_Init()) {
        QE_LOG(ERROR, "Database initialization failed");
        this->status_details = EIO;
    }

    if (!dbif_->Db_SetTablespace(qe_->keyspace())) {
        QE_LOG(ERROR,  ": Create/Set KEYSPACE: " <<
           g_viz_constants.COLLECTOR_KEYSPACE << " FAILED");
        this->status_details = EIO;
    }   
    for (std::vector<GenDb::NewCf>::const_iterator it = vizd_tables.begin();
            it != vizd_tables.end(); it++) {
        if (!dbif_->Db_UseColumnfamily(*it)) {
            QE_LOG(ERROR, "Database initialization:Db_UseColumnfamily failed");
            this->status_details = EIO;
        }
    }
    for (std::vector<GenDb::NewCf>::const_iterator it = vizd_stat_tables.begin();
            it != vizd_stat_tables.end(); it++) {
        if (!dbif_->Db_UseColumnfamily(*it)) {
            QE_LOG(ERROR, "Database initialization:Db_UseColumnfamily failed");
            this->status_details = EIO;
        }
    }
    for (std::vector<GenDb::NewCf>::const_iterator it = vizd_session_tables.begin();
            it != vizd_session_tables.end(); it++) {
        if (!dbif_->Db_UseColumnfamily(*it)) {
            QE_LOG(ERROR, "Database initialization:Db_UseColumnfamily failed");
            this->status_details = EIO;
        }
    }
    if (this->status_details != 0) {
        // Update connection info
        ConnectionState::GetInstance()->Update(ConnectionType::DATABASE,
            std::string(), ConnectionStatus::DOWN, dbif_->Db_GetEndpoints(),
            std::string());
    } else {
        // Update connection info
        ConnectionState::GetInstance()->Update(ConnectionType::DATABASE,
            std::string(), ConnectionStatus::UP, dbif_->Db_GetEndpoints(),
            std::string());
    }
    dbif_->Db_SetInitDone(true);
    Init(qid, json_api_data, or_number);
}

AnalyticsQuery::AnalyticsQuery(const std::string& qid,
    GenDbIfPtr dbif_ptr,
    std::map<std::string, std::string> json_api_data,
    int or_number,
    const std::vector<query_result_unit_t> * where_info,
    const TtlMap &ttlmap, int batch, int total_batches,
    QueryEngine* qe,
    void *handle) :
    QueryUnit(NULL, this),
    dbif_(dbif_ptr),
    query_id(qid),
    filter_qe_logs(true),
    json_api_data_(json_api_data),
    where_info_(where_info),
    ttlmap_(ttlmap),
    where_start_(0), 
    select_start_(0), 
    postproc_start_(0),
    merge_needed(false),
    parallel_batch_num(batch),
    total_parallel_batches(total_batches),
    processing_needed(true),
    qe_(qe),
    handle_(handle),
    stats_(nullptr) {
    Init(qid, json_api_data, or_number);
}

QueryEngine::QueryEngine(EventManager *evm,
            vector<string> redis_ip_ports,
            const std::string & redis_password,
            const bool redis_ssl_enable,
            const std::string & redis_keyfile,
            const std::string & redis_certfile,
            const std::string & redis_ca_cert,
            int max_tasks, int max_slice,
            const std::string & cassandra_user,
            const std::string & cassandra_password,
            bool cassandra_use_ssl,
            const std::string & cassandra_ca_certs,
            const std::string &host_ip) :
        qosp_(new QEOpServerProxy(evm,
            this, redis_ip_ports, redis_password, redis_ssl_enable, redis_keyfile,
            redis_certfile, redis_ca_cert, host_ip, max_tasks)),
        evm_(evm),
        cassandra_ports_(0),
        cassandra_user_(cassandra_user),
        cassandra_password_(cassandra_password),
        cassandra_use_ssl_(cassandra_use_ssl),
        cassandra_ca_certs_(cassandra_ca_certs)
{
    max_slice_ =  max_slice;
    // default keyspace
    keyspace_ = g_viz_constants.COLLECTOR_KEYSPACE_CQL;
    init_vizd_tables();

    // Initialize database connection
    QE_LOG_NOQID(DEBUG, "Initializing QE without database!");

    ttlmap_ = g_viz_constants.TtlValuesDefault;
    max_tasks_ = max_tasks;
}

QueryEngine::QueryEngine(EventManager *evm,
            std::vector<std::string> cassandra_ips,
            std::vector<int> cassandra_ports,
            vector<string> redis_ip_ports,
            const std::string & redis_password,
            const bool redis_ssl_enable,
            const std::string & redis_keyfile,
            const std::string & redis_certfile,
            const std::string & redis_ca_cert,
            int max_tasks, int max_slice,
            const std::string & cassandra_user,
            const std::string & cassandra_password,
            bool cassandra_use_ssl,
            const std::string & cassandra_ca_certs,
            const std::string & cluster_id,
            const std::string &host_ip) :
        qosp_(new QEOpServerProxy(evm,
            this, redis_ip_ports, redis_password, redis_ssl_enable, redis_keyfile,
            redis_certfile, redis_ca_cert, host_ip, max_tasks)),
        evm_(evm),
        cassandra_ports_(cassandra_ports),
        cassandra_ips_(cassandra_ips),
        cassandra_user_(cassandra_user),
        cassandra_password_(cassandra_password),
        cassandra_use_ssl_(cassandra_use_ssl),
        cassandra_ca_certs_(cassandra_ca_certs) {
        dbif_.reset(new cass::cql::CqlIf(evm, cassandra_ips,
            cassandra_ports[0], cassandra_user, cassandra_password,
            cassandra_use_ssl_, cassandra_ca_certs_));
        if (cluster_id.empty()) {
            keyspace_ = g_viz_constants.COLLECTOR_KEYSPACE_CQL;
        } else {
            keyspace_ = g_viz_constants.COLLECTOR_KEYSPACE_CQL + '_' + cluster_id;
        }
    max_slice_ = max_slice;
    max_tasks_ = max_tasks;
    oldDataExists = true;
    init_vizd_tables();

    // Initialize database connection
    QE_TRACE_NOQID(DEBUG, "Initializing database");

    boost::system::error_code ec;
    int retries = 0;
    bool retry = true;
    while (retry == true) {
        retry = false;

        if (!dbif_->Db_Init()) {
            QE_LOG_NOQID(ERROR, "Database initialization failed");
            retry = true;
        }

        if (!retry) {
            if (!dbif_->Db_SetTablespace(keyspace_)) {
                QE_LOG_NOQID(ERROR,  ": Create/Set KEYSPACE: " <<
                             keyspace_ << " FAILED");
                retry = true;
            }
        }

        if (!retry) {
            for (std::vector<GenDb::NewCf>::const_iterator it = vizd_tables.begin();
                    it != vizd_tables.end(); it++) {
                if (!dbif_->Db_UseColumnfamily(*it)) {
                    retry = true;
                    break;
                }
            }
        }

        if (!retry) {
            for (std::vector<GenDb::NewCf>::const_iterator it =
                    vizd_stat_tables.begin();
                    it != vizd_stat_tables.end(); it++) {
                if (!dbif_->Db_UseColumnfamily(*it)) {
                    retry = true;
                    break;
                }
            }

        }

        if (!retry) {
            for (std::vector<std::string>::const_iterator it = 
                    g_viz_constants._STATS_TABLES.begin();
                    it != g_viz_constants._STATS_TABLES.end() - 1; it++) {
                if (!dbif_->Db_UseColumnfamily(*it)) {
                    oldDataExists = false;
                    QE_LOG_NOQID(DEBUG, "Older table does not exist. will query only the new table");
                    break;
                }
            }
        }
        if (oldDataExists) {
            QE_LOG_NOQID(DEBUG, "Older table exists. will query both the tables");
        }

        if (!retry) {
            for (std::vector<GenDb::NewCf>::const_iterator it =
                    vizd_session_tables.begin();
                    it != vizd_session_tables.end(); it++) {
                if (!dbif_->Db_UseColumnfamily(*it)) {
                    retry = true;
                    break;
                }
            }

        }

        if (retry) {
            std::stringstream ss;
            ss << "initialization of database failed. retrying " << retries++ << " time";
            // Update connection info
            ConnectionState::GetInstance()->Update(ConnectionType::DATABASE,
                std::string(), ConnectionStatus::DOWN,
                dbif_->Db_GetEndpoints(), std::string());
            Q_E_LOG_LOG("QeInit", SandeshLevel::SYS_WARN, ss.str());
            dbif_->Db_Uninit();
            sleep(5);
        }
    }
    {
        bool init_done = false;
        retries = 0;
        while (!init_done && retries < 12) {
            init_done = true;

            GenDb::ColList col_list;
            std::string cfname = g_viz_constants.SYSTEM_OBJECT_TABLE;
            GenDb::DbDataValueVec key;
            key.push_back(g_viz_constants.SYSTEM_OBJECT_ANALYTICS);

            bool ttl_cached[TtlType::GLOBAL_TTL+1];
            for (int ttli=0; ttli<=TtlType::GLOBAL_TTL; ttli++)
                ttl_cached[ttli] = false;

            if (dbif_->Db_GetRow(&col_list, cfname, key,
                GenDb::DbConsistency::LOCAL_ONE)) {
                for (GenDb::NewColVec::iterator it = col_list.columns_.begin();
                        it != col_list.columns_.end(); it++) {
                    std::string col_name;
                    try {
                        col_name = boost::get<std::string>(it->name->at(0));
                    } catch (boost::bad_get& ex) {
                        QE_LOG_NOQID(ERROR, __func__ << ": Exception on col_name get");
                        break;
                    }
                    if (col_name == g_viz_constants.SYSTEM_OBJECT_GLOBAL_DATA_TTL) {
                            try {
                                ttlmap_.insert(std::make_pair(TtlType::GLOBAL_TTL, boost::get<uint64_t>(it->value->at(0))));
                                ttl_cached[TtlType::GLOBAL_TTL] = true;
                            } catch (boost::bad_get& ex) {
                                QE_LOG_NOQID(ERROR, __func__ << "Exception for boost::get, what=" << ex.what());
                            }
                    } else if (col_name == g_viz_constants.SYSTEM_OBJECT_CONFIG_AUDIT_TTL) {
                            try {
                                ttlmap_.insert(std::make_pair(TtlType::CONFIGAUDIT_TTL, boost::get<uint64_t>(it->value->at(0))));
                                ttl_cached[TtlType::CONFIGAUDIT_TTL] = true;
                            } catch (boost::bad_get& ex) {
                                QE_LOG_NOQID(ERROR, __func__ << "Exception for boost::get, what=" << ex.what());
                            }
                    } else if (col_name == g_viz_constants.SYSTEM_OBJECT_STATS_DATA_TTL) {
                            try {
                                ttlmap_.insert(std::make_pair(TtlType::STATSDATA_TTL, boost::get<uint64_t>(it->value->at(0))));
                                ttl_cached[TtlType::STATSDATA_TTL] = true;
                            } catch (boost::bad_get& ex) {
                                QE_LOG_NOQID(ERROR, __func__ << "Exception for boost::get, what=" << ex.what());
                            }
                    } else if (col_name == g_viz_constants.SYSTEM_OBJECT_FLOW_DATA_TTL) {
                            try {
                                ttlmap_.insert(std::make_pair(TtlType::FLOWDATA_TTL, boost::get<uint64_t>(it->value->at(0))));
                                ttl_cached[TtlType::FLOWDATA_TTL] = true;
                            } catch (boost::bad_get& ex) {
                                QE_LOG_NOQID(ERROR, __func__ << "Exception for boost::get, what=" << ex.what());
                            }
                    }
                }
            }
            for (int ttli=0; ttli<=TtlType::GLOBAL_TTL; ttli++)
                if (ttl_cached[ttli] == false)
                    init_done = false;

            retries++;
            if (!init_done)
                sleep(5);
        }
        if (!init_done) {
            ttlmap_ = g_viz_constants.TtlValuesDefault;
            QE_LOG_NOQID(ERROR, __func__ << "ttls are set manually");
        }
    }
    dbif_->Db_SetInitDone(true);
    // Update connection info
    ConnectionState::GetInstance()->Update(ConnectionType::DATABASE,
        std::string(), ConnectionStatus::UP, dbif_->Db_GetEndpoints(),
        std::string());
}

QueryEngine::~QueryEngine() {
    if (dbif_) {
        dbif_->Db_Uninit();
        dbif_->Db_SetInitDone(false);
    }
}

using std::vector;

int
QueryEngine::QueryPrepare(QueryParams qp,
        std::vector<uint64_t> &chunk_size,
        bool & need_merge, bool & map_output,
        std::string& where, uint32_t& wterms,
        std::string& select, std::string& post,
        uint64_t& time_period, 
        std::string &table) {
    string& qid = qp.qid;
    QE_LOG_NOQID(INFO, 
             " Got Query to prepare for QID " << qid);
    int ret_code;
    if (cassandra_ports_.size() == 1 && cassandra_ports_[0] == 0) {
        chunk_size.push_back(999);
        need_merge = false;
        map_output = false;
        ret_code = 0;
        table = string("ObjectCollectorInfo");
    } else {
        AnalyticsQuery *q;
        q = new AnalyticsQuery(qid, dbif_, qp.terms, -1, NULL, ttlmap_, 0,
                qp.maxChunks, this);
        chunk_size.clear();
        q->get_query_details(need_merge, map_output, chunk_size,
            where, wterms ,select, post, time_period, ret_code);
        table = q->table();
        delete q;
    }
    return ret_code;
}

bool
QueryEngine::QueryAccumulate(QueryParams qp,
        const QEOpServerProxy::BufferT& input,
        QEOpServerProxy::BufferT& output) {

    QE_TRACE_NOQID(DEBUG, "Creating analytics query object for merge_processing");
    AnalyticsQuery *q;
    q = new AnalyticsQuery(qp.qid, dbif_, qp.terms, -1, NULL, ttlmap_, 1,
                qp.maxChunks, this);
    QE_TRACE_NOQID(DEBUG, "Calling merge_processing");
    bool ret = q->merge_processing(input, output);
    delete q;
    return ret;
}

bool
QueryEngine::QueryFinalMerge(QueryParams qp,
        const std::vector<boost::shared_ptr<QEOpServerProxy::BufferT> >& inputs,
        QEOpServerProxy::BufferT& output) {

    QE_TRACE_NOQID(DEBUG, "Creating analytics query object for final_merge_processing");
    AnalyticsQuery *q;
    q = new AnalyticsQuery(qp.qid, dbif_, qp.terms, -1, NULL, ttlmap_, 1,
                qp.maxChunks, this);
    QE_TRACE_NOQID(DEBUG, "Calling final_merge_processing");
    bool ret = q->final_merge_processing(inputs, output);
    delete q;
    return ret;
}

bool
QueryEngine::QueryFinalMerge(QueryParams qp,
        const std::vector<boost::shared_ptr<QEOpServerProxy::OutRowMultimapT> >& inputs,
        QEOpServerProxy::OutRowMultimapT& output) {
    QE_TRACE_NOQID(DEBUG, "Creating analytics query object for final_merge_processing");
    AnalyticsQuery *q;
    q = new AnalyticsQuery(qp.qid, dbif_, qp.terms, -1, NULL, ttlmap_, 1,
                qp.maxChunks, this);

    if (!q->is_stat_table_query(q->table())
        && !q->is_session_query(q->table())
        && !q->is_flow_query(q->table())) {
        QE_TRACE_NOQID(DEBUG, "MultiMap merge_final is for Stats only");
        delete q;
        return false;
    }
    QE_TRACE_NOQID(DEBUG, "Calling final_merge_processing for Stats");

    q->selectquery_->stats_->MergeFinal(inputs, output);
    // apply limit
    if (q->postprocess_->limit &&
        output.size() > (size_t)q->postprocess_->limit) {
            QEOpServerProxy::OutRowMultimapT::iterator it = output.begin();
            std::advance(it, (size_t)q->postprocess_->limit);
            output.erase(it, output.end());
    }
    delete q;
    return true;   
}

// Query Execution of WHERE term
bool
QueryEngine::QueryExecWhere(void * handle, QueryParams qp, uint32_t chunk,
        uint32_t or_number)
{
    string& qid = qp.qid;
    QE_TRACE_NOQID(DEBUG,
             " Got Where Query to execute for QID " << qid << " chunk:"<< chunk);
    if (cassandra_ports_.size() == 1 && cassandra_ports_[0] == 0) {
        std::unique_ptr<std::vector<query_result_unit_t> > where_output(
                new std::vector<query_result_unit_t>());
        QE_TRACE_NOQID(DEBUG, " Finished NULL query processing for QID " << qid << " chunk:" << chunk);
        QEOpServerProxy::QPerfInfo qperf(0,0,0);
        qperf.error = 0;

        qosp_->QueryResult(handle, qperf, std::auto_ptr<std::vector<query_result_unit_t>>(where_output.release()));
        return true;
    }
    boost::shared_ptr<AnalyticsQuery> q(new AnalyticsQuery(qid, dbif_, qp.terms,
        or_number, NULL, ttlmap_, chunk, qp.maxChunks, this, handle));
    // populate into a vector mainted by QOSP
    qosp_->AddAnalyticsQuery(qid, q);
    QE_TRACE_NOQID(DEBUG, " Finished parsing and starting where for QID " << qid << " chunk:" << chunk);

    q->where_start_ = UTCTimestampUsec();
    // Bind the callback function to where query
    q->wherequery_->where_query_cb_ = boost::bind(&QEOpServerProxy::QueryResult, qosp_.get(), _1, _2, _3);
    q->query_status = q->wherequery_->process_query();
    bool query_status_ = false;
    switch (q->query_status) {
        case QUERY_PROCESSING_NOT_STARTED:
            /* should not come here */
        case QUERY_FAILURE:
            break;
        case QUERY_SUCCESS:
            q->qperf_.chunk_where_time =
                static_cast<uint32_t>((UTCTimestampUsec() - q->where_start_)
                /1000);
            q->qperf_.error = q->status_details;
            qosp_->QueryResult(q->handle_, q->qperf_, std::auto_ptr<std::vector<query_result_unit_t>>(q->wherequery_->where_result_.release()));
        case QUERY_IN_PROGRESS:
            query_status_ = true;
            break;
    }
    return query_status_;
}

// Query Execution of SELECT and post-processing
bool
QueryEngine::QueryExec(void * handle, QueryParams qp, uint32_t chunk,
        const std::vector<query_result_unit_t> * where_info)
{
    string& qid = qp.qid;
    QE_TRACE_NOQID(DEBUG,
             " Got Query to execute for QID " << qid << " chunk:"<< chunk);
    //GenDb::GenDbIf *db_if = dbif_.get();
    if (cassandra_ports_.size() == 1 && cassandra_ports_[0] == 0) {
        std::unique_ptr<QEOpServerProxy::BufferT> final_output(new QEOpServerProxy::BufferT);
        QEOpServerProxy::OutRowT outrow = boost::assign::map_list_of(
            "MessageTS", "1368037623434740")(
            "Messagetype", "IFMapString")(
            "ModuleId", "ControlNode")(
            "Source","b1s1")(
            "ObjectLog","\n<IFMapString type=\"sandesh\"><message type=\"string\" identifier=\"1\">Cancelling Response timer.</message><file type=\"string\" identifier=\"-32768\">src/ifmap/client/ifmap_state_machine.cc</file><line type=\"i32\" identifier=\"-32767\">578</line></IFMapString>");
        QEOpServerProxy::MetadataT metadata;
        std::unique_ptr<QEOpServerProxy::OutRowMultimapT> final_moutput(new QEOpServerProxy::OutRowMultimapT);
        for (int i = 0 ; i < 100; i++)
            final_output->push_back(std::make_pair(outrow, metadata));
        QE_TRACE_NOQID(DEBUG, " Finished query processing for QID " << qid << " chunk:" << chunk);
        QEOpServerProxy::QPerfInfo qperf(0,0,0);
        qperf.error = 0;
        qosp_->QueryResult(handle, qperf, std::auto_ptr<QEOpServerProxy::BufferT>(final_output.release()), std::auto_ptr<QEOpServerProxy::OutRowMultimapT>(final_moutput.release()));
        return true;
    }
    AnalyticsQuery *q;
    q = new AnalyticsQuery(qid, dbif_, qp.terms, -1, where_info, ttlmap_, chunk,
                qp.maxChunks, this);

    QE_TRACE_NOQID(DEBUG, " Finished parsing and starting processing for QID " << qid << " chunk:" << chunk); 
    q->process_query(); 

    QE_TRACE_NOQID(DEBUG, " Finished query processing for QID " << qid << " chunk:" << chunk);
    q->qperf_.error = q->status_details;
    qosp_->QueryResult(handle, q->qperf_, std::auto_ptr<QEOpServerProxy::BufferT>(q->final_result.release()), std::auto_ptr<QEOpServerProxy::OutRowMultimapT>(q->final_mresult.release()));
    delete q;
    return true;
}

bool QueryEngine::GetCumulativeStats(std::vector<GenDb::DbTableInfo> *vdbti,
        GenDb::DbErrors *dbe, std::vector<GenDb::DbTableInfo> *vstats_dbti)
        const {
    {
        tbb::mutex::scoped_lock lock(smutex_);
        stable_stats_.GetCumulative(vstats_dbti);
    }
    return dbif_->Db_GetCumulativeStats(vdbti, dbe);
}


std::ostream &operator<<(std::ostream &out, query_result_unit_t& res)
{
    out << "T:" << res.timestamp << " : Need to extract other information";
#if 0
    out << "T:" << res.timestamp << " : ";

    if (res.info.length() < 48) {
        boost::uuids::uuid tmp_u;
        res.get_uuid(tmp_u);
        out << " UUID:" << tmp_u;
    }
#endif

    return out;
}

bool
AnalyticsQuery::is_stat_table_query(const std::string & tname) {
    if (tname.compare(0, g_viz_constants.STAT_VT_PREFIX.length(),
            g_viz_constants.STAT_VT_PREFIX)) {
        return false;
    }
    return true;
}

bool
AnalyticsQuery::is_session_query(const std::string & tname) {
    return (tname == g_viz_constants.SESSION_SERIES_TABLE ||
            tname == g_viz_constants.SESSION_RECORD_TABLE);
}

bool
AnalyticsQuery::is_stat_fieldnames_table_query(const std::string & tname) {
    if (tname.compare(0, g_viz_constants.STAT_VT_FIELDNAMES_PREFIX.length(),
            g_viz_constants.STAT_VT_FIELDNAMES_PREFIX)) {
        return false;
    }
    return true;
}

bool AnalyticsQuery::is_flow_query(const std::string & tname)
{
    return ((tname == g_viz_constants.FLOW_SERIES_TABLE) ||
        (tname == g_viz_constants.FLOW_TABLE));
}

// validation functions
bool AnalyticsQuery::is_message_table_query(const std::string &tname)
{
    return (tname == g_viz_constants.MESSAGE_TABLE);
}

bool AnalyticsQuery::is_message_table_query()
{
    return (table_ == g_viz_constants.MESSAGE_TABLE);
}

bool AnalyticsQuery::is_object_table_query(const std::string &tname)
{
    return (
        (tname != g_viz_constants.MESSAGE_TABLE) &&
        (tname != g_viz_constants.FLOW_TABLE) &&
        (tname != g_viz_constants.FLOW_SERIES_TABLE) &&
        (tname != g_viz_constants.OBJECT_VALUE_TABLE) &&
        !is_stat_table_query(tname) &&
        !is_session_query(tname));
}

bool AnalyticsQuery::is_valid_where_field(const std::string& where_field)
{
    for(size_t i = 0; i < g_viz_constants._TABLES.size(); i++)
    {
        if (g_viz_constants._TABLES[i].name == table_)
        {
            for (size_t j = 0; 
                j < g_viz_constants._TABLES[i].schema.columns.size(); j++)
            {
                if ((g_viz_constants._TABLES[i].schema.columns[j].name ==
                        where_field) &&
                        g_viz_constants._TABLES[i].schema.columns[j].index)
                    return true;
            }
            return false;
        }
    }
    if (is_stat_table_query(table_)) {
        AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
        if (m_query->stats().is_stat_table_static()) {
            StatsQuery::column_t cdesc = m_query->stats().get_column_desc(where_field);
            if (cdesc.index) return true;
        } else {
            // For dynamic Stat Table queries, allow anything in the where clause
            return true;
        }
    }
    return true;
}

bool AnalyticsQuery::is_valid_sort_field(const std::string& sort_field) {
    if (
        (sort_field == SELECT_PACKETS) ||
        (sort_field == SELECT_BYTES) ||
        (sort_field == SELECT_SUM_PACKETS) ||
        (sort_field == SELECT_SUM_BYTES)
        )
        return true;

    return selectquery_->is_present_in_select_column_fields(sort_field);
}

std::string AnalyticsQuery::get_column_field_datatype(
                                    const std::string& column_field) {
    for(size_t i = 0; i < g_viz_constants._TABLES.size(); i++) {
        if (g_viz_constants._TABLES[i].name == table_) {
            for (size_t j = 0; 
                 j < g_viz_constants._TABLES[i].schema.columns.size(); j++) {
                if (g_viz_constants._TABLES[i].schema.columns[j].name == 
                        column_field) {
                    return g_viz_constants._TABLES[i].schema.columns[j].datatype;
                }
            }
            return std::string("");
        }
    }
    if (stats_.get()) {
        StatsQuery::column_t vt = stats().get_column_desc(column_field);
        if (vt.datatype == QEOpServerProxy::STRING)
            return string("string");
        else if (vt.datatype == QEOpServerProxy::UINT64)
            return string("int");
        else if (vt.datatype == QEOpServerProxy::DOUBLE)
            return string("double");
        else
            return string("");
    }
    return std::string("");
}

std::map< std::string, int > trace_enable_map;
void TraceEnable::HandleRequest() const
{
    TraceEnableRes *resp = new TraceEnableRes;
    std::string status;
    std::string trace_type = get_TraceType();
    if (trace_type == WHERE_RESULT_TRACE || trace_type == SELECT_RESULT_TRACE ||
        trace_type == POSTPROCESS_RESULT_TRACE) {
        if (get_enable())
        {
            trace_enable_map.insert(std::make_pair(trace_type, 1));
            status = "Trace buffer Enabled";
        } else {
            trace_enable_map.erase(trace_type);
            status = "Trace buffer Disabled";
        }
    } else {
        status = "Invalid Trace buffer";
    }
    resp->set_enable_disable_status(status);
    resp->set_TraceType(trace_type);
    resp->set_context(context());
    resp->set_more(false);
    resp->Response();
}

void TraceStatusReq::HandleRequest() const {
    std::vector<std::string> trace_buf_list;
    trace_buf_list.push_back(WHERE_RESULT_TRACE);
    trace_buf_list.push_back(SELECT_RESULT_TRACE);
    trace_buf_list.push_back(POSTPROCESS_RESULT_TRACE);
    std::vector<TraceStatusInfo> trace_status_list;
    for (std::vector<std::string>::const_iterator it = trace_buf_list.begin();
         it != trace_buf_list.end(); ++it) {
        TraceStatusInfo trace_status;
        trace_status.set_TraceType(*it);
        if (IS_TRACE_ENABLED(*it)) {
            trace_status.set_enable_disable("Enabled");
        } else {
            trace_status.set_enable_disable("Disabled");
        }
        trace_status_list.push_back(trace_status);
    }
    TraceStatusRes *resp = new TraceStatusRes;
    resp->set_trace_status_list(trace_status_list);
    resp->set_context(context());
    resp->set_more(false);
    resp->Response();
}

bool QueryEngine::GetDiffStats(std::vector<GenDb::DbTableInfo> *vdbti,
    GenDb::DbErrors *dbe, std::vector<GenDb::DbTableInfo> *vstats_dbti) {
    {
        tbb::mutex::scoped_lock lock(smutex_);
        stable_stats_.GetDiffs(vstats_dbti);
    }
    return dbif_->Db_GetStats(vdbti, dbe);
}

bool QueryEngine::GetCqlStats(cass::cql::DbStats *stats) const {
    cass::cql::CqlIf *cql_if(dynamic_cast<cass::cql::CqlIf *>(dbif_.get()));
    if (cql_if == NULL) {
        return false;
    }
    return cql_if->Db_GetCqlStats(stats);
}

bool QueryEngine::GetCqlMetrics(cass::cql::Metrics *metrics) const {
    cass::cql::CqlIf *cql_if(dynamic_cast<cass::cql::CqlIf *>(dbif_.get()));
    if (cql_if == NULL) {
        return false;
    }
    cql_if->Db_GetCqlMetrics(metrics);
    return true;
}

void ShowQEDbStatsReq::HandleRequest() const {
    std::vector<GenDb::DbTableInfo> vdbti, vstats_dbti;
    GenDb::DbErrors dbe;
    QESandeshContext *qec = static_cast<QESandeshContext *>(
                                        Sandesh::client_context());
    assert(qec);
    ShowQEDbStatsResp *resp(new ShowQEDbStatsResp);
    qec->QE()->GetCumulativeStats(&vdbti, &dbe, &vstats_dbti);
    cass::cql::Metrics cmetrics;
    qec->QE()->GetCqlMetrics(&cmetrics);
    resp->set_table_info(vdbti);
    resp->set_errors(dbe);
    resp->set_statistics_table_info(vstats_dbti);
    resp->set_cql_metrics(cmetrics);
    resp->set_context(context());
    resp->Response();
}

