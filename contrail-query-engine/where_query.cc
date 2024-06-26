/*
 * Copyright (c) 2013 Juniper Networks, Inc. All rights reserved.
 */

#include <cstdlib>
#include <limits>
#include <string>
#include <sstream>
#include <boost/foreach.hpp>
#include <boost/algorithm/string/case_conv.hpp>
#include "rapidjson/document.h"
#include <boost/foreach.hpp>
#include "query.h"
#include "json_parse.h"
#include "base/regex.h"
#include "base/string_util.h"
#include "database/gendb_constants.h"
#include "database/gendb_if.h"
#include "utils.h"
#include "query.h"
#include "stats_query.h"

using contrail::regex;
using contrail::regex_match;
using contrail::regex_search;
using std::string;

static std::string ToString(const contrail_rapidjson::Value& value_value) {
    std::string svalue;
    if (value_value.IsString())
    {
        svalue = value_value.GetString();
    } else if (value_value.IsInt()){
        int int_value;
        std::ostringstream convert;
        int_value = value_value.GetInt();
        convert << int_value;
        svalue = convert.str();
    } else if (value_value.IsUint()) {
        uint32_t uint_value;
        std::ostringstream convert;
        uint_value = value_value.GetUint();
        convert << uint_value;
        svalue = convert.str();
    } else if (value_value.IsDouble()) {
        double dbl_value;
        std::ostringstream convert;
        dbl_value = value_value.GetDouble();
        convert << dbl_value;
        svalue = convert.str();
    }
    return svalue;
}

static GenDb::DbDataValue ToDbDataValue(const std::string& value, QEOpServerProxy::VarType desc) {
    GenDb::DbDataValue smpl;
    if (desc == QEOpServerProxy::STRING ||
        desc == QEOpServerProxy::MAP_ELEM ||
        desc == QEOpServerProxy::LIST) {
        smpl = value;
    } else if (desc == QEOpServerProxy::UINT64) {
        smpl = (uint64_t) strtoul(value.c_str(), NULL, 10);
    } else if (desc == QEOpServerProxy::DOUBLE) {
        smpl = (double) strtod(value.c_str(), NULL); 
    }
    return smpl;
}

static GenDb::DbDataValue ToDbDataValue(const contrail_rapidjson::Value& val) {
    GenDb::DbDataValue ret;
    if (val.IsString()) {
        ret = std::string(val.GetString());
    } else if (val.IsUint()) {
        ret = (uint64_t) val.GetUint();
    } else if (val.IsInt()) {
        ret = (uint64_t) val.GetInt();
    } else if (val.IsDouble()) {
        ret = (double) val.GetDouble();
    }
    return ret;
}

static QEOpServerProxy::VarType ToDbDataType(string val) {
    QEOpServerProxy::VarType ret = QEOpServerProxy::BLANK;
    if (val == "int") {
        ret = QEOpServerProxy::UINT64;
    } else if (val == "string") {
        ret = QEOpServerProxy::STRING;
    } else if (val == "uuid") {
        ret = QEOpServerProxy::UUID;
    } else if (val == "double") {
        ret = QEOpServerProxy::DOUBLE;
    } else if (val == "map") {
        ret = QEOpServerProxy::MAP_ELEM;
    } else if (val == "set" || val == "list") {
        ret = QEOpServerProxy::LIST;
    }
    return ret;
}

static StatsQuery::column_t get_column_desc(std::map<std::string,StatsQuery::column_t> table_schema, std::string pname) {
    StatsQuery::column_t cdesc;
    std::map<std::string,StatsQuery::column_t>::const_iterator st =
            table_schema.find(pname);
    if (st!=table_schema.end()) {
        cdesc = st->second;
    } else {
        size_t pos = pname.find_last_of(".");
        std::string mapstr(pname.substr(0,pos) + ".*");
        st = table_schema.find(mapstr);
        if (st != table_schema.end()) {
            cdesc = st->second;
        } else  {
            cdesc.datatype = QEOpServerProxy::BLANK;
            cdesc.index = false;
            cdesc.output = false;
        }
    }
    return cdesc;
}

bool
WhereQuery::StatTermParse(QueryUnit *main_query, const contrail_rapidjson::Value& where_term,
        std::string& pname, match_op& pop, GenDb::DbDataValue& pval, GenDb::DbDataValue& pval2,
        std::string& sname, match_op& sop, GenDb::DbDataValue& sval, GenDb::DbDataValue& sval2) {

    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
    QE_ASSERT(m_query->is_stat_table_query(m_query->table()));

    contrail_rapidjson::Document dd;
    std::string srvalstr, srval2str;

    if (!where_term.HasMember(WHERE_MATCH_NAME))
        return false;
    const contrail_rapidjson::Value& name_value = where_term[WHERE_MATCH_NAME];
    if (!name_value.IsString()) return false;
    pname = name_value.GetString();

    const contrail_rapidjson::Value& prval = where_term[WHERE_MATCH_VALUE];
    if (!((prval.IsString() || prval.IsNumber()))) return false;
    contrail_rapidjson::Value prval2;
    if (where_term.HasMember(WHERE_MATCH_VALUE2)) {
        prval2.CopyFrom(where_term[WHERE_MATCH_VALUE2], dd.GetAllocator());
    }

    // For dynamic stat tables, convert types as per query json
    pval = ToDbDataValue(prval);
    pval2 = ToDbDataValue(prval2);

    if (!where_term.HasMember(WHERE_MATCH_OP))
        return false;
    const contrail_rapidjson::Value& op_value = where_term[WHERE_MATCH_OP];
    if (!op_value.IsNumber()) return false;
    pop = (match_op)op_value.GetInt();

    QE_TRACE(DEBUG, "StatTable Where Term Prefix " << pname << " val " << ToString(prval) 
            << " val2 " << ToString(prval2) << " op " << pop);

    sname = std::string(); 
    sop = (match_op)0;
    if (where_term.HasMember(WHERE_MATCH_SUFFIX)) {
        const contrail_rapidjson::Value& suffix = where_term[WHERE_MATCH_SUFFIX];
        if (suffix.IsObject()) {
            // For prefix-suffix where terms, prefix operator MUST be "EQUAL"
            if (pop != EQUAL) return false;

            // For prefix-suffix where terms, prefix value2 MUST be Null
            if (!prval2.IsNull()) return false;

            if (!suffix.HasMember(WHERE_MATCH_VALUE))
                return false;
            const contrail_rapidjson::Value& svalue_value =
                suffix[WHERE_MATCH_VALUE];
            if (!((svalue_value.IsString() || svalue_value.IsNumber()))) return false;
            srvalstr = ToString(svalue_value);
            // For dynamic stat tables, convert types as per query json
            sval = ToDbDataValue(svalue_value);

            contrail_rapidjson::Value svalue2_value;
            if (suffix.HasMember(WHERE_MATCH_VALUE2)) {
                svalue2_value.CopyFrom(suffix[WHERE_MATCH_VALUE2],
                                       dd.GetAllocator());
            }
            srval2str = ToString(svalue2_value);
            // For dynamic stat tables, convert types as per query json
            sval2 = ToDbDataValue(svalue2_value);

            if (!suffix.HasMember(WHERE_MATCH_OP))
                return false;
            const contrail_rapidjson::Value& sop_value =
                suffix[WHERE_MATCH_OP];
            if (!sop_value.IsNumber()) return false;
            sop = (match_op)sop_value.GetInt();

            if (!suffix.HasMember(WHERE_MATCH_NAME))
                return false;
            const contrail_rapidjson::Value& sname_value =
                suffix[WHERE_MATCH_NAME];
            if (!sname_value.IsString()) return false;
            sname = sname_value.GetString();
        }
        QE_TRACE(DEBUG, "StatTable Where Term Suffix" << sname << " val " <<
                 srvalstr << " val2 " << srval2str << " op " << sop);
    }

    StatsQuery::column_t cdesc;
    cdesc.datatype = QEOpServerProxy::BLANK;
    std::map<std::string, StatsQuery::column_t> table_schema;
    if (m_query->stats().is_stat_table_static()) {
        // For static tables, check that prefix is valid and convert types as per schema
        cdesc = m_query->stats().get_column_desc(pname);
    } else {
        // Get the stable schema from query if sent
        AnalyticsQuery *aQuery = (AnalyticsQuery *)m_query;
        std::map<std::string, std::string>::iterator iter, iter2;
        iter = aQuery->json_api_data_.find(QUERY_TABLE_SCHEMA);
        if (iter != aQuery->json_api_data_.end()) {
            contrail_rapidjson::Document d;
            std::string json_string = "{ \"schema\" : " + iter->second + " }";
            d.Parse<0>(const_cast<char *>(json_string.c_str()));
            const contrail_rapidjson::Value& json_schema = d["schema"];
            // If schema is not passed, proceed without suffix information
            if (json_schema.Size() == 0) {
                return true;
            }
            for (contrail_rapidjson::SizeType j = 0; j<json_schema.Size(); j++) {
                if (!(json_schema[j].HasMember(WHERE_MATCH_NAME) &&
                      json_schema[j].HasMember(QUERY_TABLE_SCHEMA_DATATYPE) &&
                      json_schema[j].HasMember(QUERY_TABLE_SCHEMA_INDEX) &&
                      json_schema[j].HasMember(QUERY_TABLE_SCHEMA_SUFFIXES)))
                    return false;
                const contrail_rapidjson::Value& name = json_schema[j][WHERE_MATCH_NAME];
                const contrail_rapidjson::Value&  datatype =
                        json_schema[j][QUERY_TABLE_SCHEMA_DATATYPE];
                const contrail_rapidjson::Value& index =
                        json_schema[j][QUERY_TABLE_SCHEMA_INDEX];
                const contrail_rapidjson::Value& suffixes =
                        json_schema[j][QUERY_TABLE_SCHEMA_SUFFIXES];
                StatsQuery::column_t cdesc;
                std::string vstr = datatype.GetString();
                cdesc.datatype = ToDbDataType(vstr);
                cdesc.index = index.GetBool()? true : false;

                if (suffixes.IsArray() && suffixes.Size() > 0) {
                    for (contrail_rapidjson::SizeType k = 0; k<suffixes.Size(); k++) {
                        const contrail_rapidjson::Value& suffix_name = suffixes[k];
                        cdesc.suffixes.insert(suffix_name.GetString());
                    }
                }
                table_schema[name.GetString()] = cdesc;
            }
        }
        cdesc = get_column_desc(table_schema, pname);
    }

    if (cdesc.datatype == QEOpServerProxy::BLANK) return false;
    if (!cdesc.index) return false;
    if (cdesc.datatype == QEOpServerProxy::LIST && pop != CONTAINS) return false;

    QE_TRACE(DEBUG, "StatTable Where prefix Schema match " << cdesc.datatype);
    // Now fill in the prefix value and value2 based on types in schema
    std::string vstr = ToString(prval);
    pval = ToDbDataValue(vstr, cdesc.datatype);
    if (!prval2.IsNull()) {
        std::string vstr = ToString(prval2);
        pval2 = ToDbDataValue(vstr, cdesc.datatype);
    }

    if (cdesc.suffixes.empty()) {
        // We need to use a onetag cf as the index
        if (!sname.empty()) return false;
        if (sop) return false;
        if (!srvalstr.empty()) return false;
        if (!srval2str.empty()) return false;
    } else {
        // We will need to use a twotag cf as the index
        if (sname.empty()) {
            // Where Query did not specify a suffix. Insert a NULL suffix
            sname = *(cdesc.suffixes.begin());

            // The suffix attribute MUST exist in the schema
            StatsQuery::column_t cdesc2;
            if (m_query->stats().is_stat_table_static()) {
                cdesc2 = m_query->stats().get_column_desc(sname);
            } else {
                cdesc2 = get_column_desc(table_schema, sname);;
            }
                
            if (cdesc2.datatype == QEOpServerProxy::STRING) {
                sval = std::string("");
            } else if (cdesc2.datatype == QEOpServerProxy::UINT64){
                sval = (uint64_t) 0;
            } else {
                QE_ASSERT(0);
            }
            QE_TRACE(DEBUG, "StatTable Where Suffix creation of " << sname);
        } else {
            // Where query specified a suffix. Check that it is valid
            if (cdesc.suffixes.find(sname)==cdesc.suffixes.end()) return false;

            // The suffix attribute MUST exist in the schema
            StatsQuery::column_t cdesc2;
            if (m_query->stats().is_stat_table_static()) {
                cdesc2 = m_query->stats().get_column_desc(sname);
            } else {
                cdesc2 = get_column_desc(table_schema, sname);;
            }
            QE_ASSERT ((cdesc2.datatype == QEOpServerProxy::STRING) ||
            (cdesc2.datatype == QEOpServerProxy::UINT64));

            // Now fill in the suffix value and value2 based on types in schema
            sval = ToDbDataValue(srvalstr, cdesc2.datatype);
            if (!srval2str.empty()) {
                sval2 = ToDbDataValue(srval2str, cdesc2.datatype);
            }
            QE_TRACE(DEBUG, "StatTable Where Suffix match of " << cdesc2.datatype);
        }
    }

    return true;
}

static bool StatSlicer(DbQueryUnit *db_query, match_op op,
        const GenDb::DbDataValue& val, const GenDb::DbDataValue& val2) {
    if (val.which() == GenDb::DB_VALUE_STRING) {
        if (!((op == EQUAL) || (op == PREFIX))) return false;
    } else {
        if (!((op == EQUAL) || (op == IN_RANGE))) return false;
    }
    db_query->cr.start_.push_back(val);
    if (op == PREFIX) {
        std::string str_smpl2(boost::get<std::string>(val) + "\x7f");
        db_query->cr.finish_.push_back(str_smpl2);
    } else if (op == IN_RANGE) {
        db_query->cr.finish_.push_back(val2);
    } else {
        db_query->cr.finish_.push_back(val);
    }
    return true;
}

bool WhereQuery::StatTermProcess(const contrail_rapidjson::Value& where_term,
        QueryUnit* and_node, QueryUnit *main_query) {

    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
    std::string pname,sname,cfname;
    match_op pop,sop;
    GenDb::DbDataValue pval, pval2, sval, sval2;

    bool res = StatTermParse(main_query, where_term,
            pname, pop, pval, pval2, sname, sop, sval, sval2);

    if (!res) return false;

    bool twotag = true;
    if ((sop==(match_op)0)&&(sname.empty())) {
        // We need to look at the single-tag stat index tables
        twotag = false;
        if (pval.which() == GenDb::DB_VALUE_STRING) {
            cfname = g_viz_constants.STATS_TABLE_BY_STR_TAG;
        } else if (pval.which() == GenDb::DB_VALUE_UINT64) {
            cfname = g_viz_constants.STATS_TABLE_BY_U64_TAG;
        } else if (pval.which() == GenDb::DB_VALUE_DOUBLE) {
            cfname = g_viz_constants.STATS_TABLE_BY_DBL_TAG;
        } else {
            QE_TRACE(DEBUG, "For single-tag index table, wrong WHERE type " <<
                    pval.which());
            return false;
        }
    } else {
        if (pval.which() == GenDb::DB_VALUE_STRING) {
            if (sval.which() == GenDb::DB_VALUE_STRING) {
                cfname = g_viz_constants.STATS_TABLE_BY_STR_STR_TAG;
            } else if (sval.which() == GenDb::DB_VALUE_UINT64) {
                cfname = g_viz_constants.STATS_TABLE_BY_STR_U64_TAG;
            } else {
                QE_TRACE(DEBUG, "For two-tag STR table, wrong WHERE suffix type " <<
                        sval.which());
                return false;
            }
        } else if (pval.which() == GenDb::DB_VALUE_UINT64) {
            if (sval.which() == GenDb::DB_VALUE_STRING) {
                cfname = g_viz_constants.STATS_TABLE_BY_U64_STR_TAG;
            } else if (sval.which() == GenDb::DB_VALUE_UINT64) {
                cfname = g_viz_constants.STATS_TABLE_BY_U64_U64_TAG;
            } else {
                QE_TRACE(DEBUG, "For two-tag U64 table, wrong WHERE suffix type " <<
                        sval.which());
                return false;
            }
        } else {
            QE_TRACE(DEBUG, "For two-tag index table, wrong WHERE prefix type " <<
                    pval.which());
            return false;
        }
    }
    QE_TRACE(DEBUG, "Query Stat Index " << cfname <<  " twotag " << twotag);
    DbQueryUnit *db_query = new DbQueryUnit(and_node, main_query);

    db_query->t_only_col = false;
    db_query->t_only_row = false;
    db_query->cfname = cfname;

    size_t tpos,apos;
    std::string tname = m_query->table();
    tpos = tname.find('.');
    apos = tname.find('.', tpos+1);

    std::string tstr = tname.substr(tpos+1, apos-tpos-1);
    std::string astr = tname.substr(apos+1, std::string::npos);

    db_query->row_key_suffix.push_back(tstr);
    db_query->row_key_suffix.push_back(astr);
    db_query->row_key_suffix.push_back(pname);

    if (twotag) {
        db_query->row_key_suffix.push_back(sname);
        if (sop==(match_op)0) {
            // We will only be using the prefix value for querying
            if (!StatSlicer(db_query, pop, pval, pval2)) return false;

            if (sval.which() == GenDb::DB_VALUE_STRING) {
                db_query->cr.start_.push_back(std::string("\x00"));
                db_query->cr.finish_.push_back(std::string("\x7f"));
            } else {
                db_query->cr.start_.push_back((uint64_t)0);
                db_query->cr.finish_.push_back((uint64_t)0xffffffffffffffff);
            }
        } else {
            // We will be using the suffix value for querying
            if (!(pop == EQUAL)) return false;
            db_query->cr.start_.push_back(pval);
            db_query->cr.finish_.push_back(pval);

            if (!StatSlicer(db_query, sop, sval, sval2)) return false;
        }

    } else {
        if (!StatSlicer(db_query, pop, pval, pval2)) return false;
    }

    return true;
}

void GetStatTableAttrName(const std::string& tname, std::string *tstr, std::string *astr) {
    size_t tpos,apos;
    tpos = tname.find('.');
    apos = tname.find('.', tpos+1);

    *tstr = tname.substr(tpos+1, apos-tpos-1);
    *astr = tname.substr(apos+1, std::string::npos);
}

void populate_stats_where_vec_list(std::vector<GenDb::WhereIndexInfoVec> *where_vec_list,
    const GenDb::WhereIndexInfoVec& where_vec_stats,
    const std::vector<GenDb::WhereIndexInfoVec>& where_vec_tags_stats) {
    uint16_t max_tags(0);
    BOOST_FOREACH(const GenDb::WhereIndexInfoVec& where_vec, where_vec_tags_stats) {
        if (max_tags < where_vec.size()) {
            max_tags = where_vec.size();
        }
    }
    if (!max_tags) {
        where_vec_list->push_back(where_vec_stats);
        return;
    } else {
        for (size_t i = 0; i < max_tags; ++i) {
            GenDb::WhereIndexInfoVec where_vec(where_vec_stats);
            BOOST_FOREACH(const GenDb::WhereIndexInfoVec& where_vec_tags, where_vec_tags_stats) {
                if (i < where_vec_tags.size()) {
                    where_vec.push_back(where_vec_tags[i]);
                }
            }
            where_vec_list->push_back(where_vec);
        }
    }
}

static inline unsigned int djb_hash (const char *str, size_t len) {
    unsigned int hash = 5381;
    for (size_t i = 0 ; i < len ; i++)
        hash = ((hash << 5) + hash) + str[i];
    return hash;
}

WhereQuery::WhereQuery(const std::string& where_json_string, int session_type,
        int is_si, int direction, int32_t or_number, QueryUnit *main_query):
    QueryUnit(main_query, main_query), direction_ing(direction),
    json_string_(where_json_string), wterms_(0) {
    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
    where_result_.reset(new std::vector<query_result_unit_t>);
    if (where_json_string == std::string(""))
    {
        if (or_number == -1) wterms_ = 1;
        DbQueryUnit *db_query = new DbQueryUnit(this, main_query);

        //TBD not sure if this will work for Message table or Object Log
        if (m_query->is_message_table_query()) {
            db_query->cfname = g_viz_constants.COLLECTOR_GLOBAL_TABLE;
            db_query->t_only_col = true;
            db_query->t_only_row = true;
        } else if 
        ((m_query->table() == g_viz_constants.FLOW_TABLE)
        || (m_query->table() == g_viz_constants.FLOW_SERIES_TABLE)) {
            DbQueryUnit *db_query_client = new DbQueryUnit(this, main_query);
            {
                db_query->cfname = g_viz_constants.SESSION_TABLE;
                db_query->row_key_suffix.push_back((uint8_t)is_si);
                db_query->row_key_suffix.push_back(
                                (uint8_t)SessionType::SERVER_SESSION);
                // starting value for clustering key range
                db_query->cr.start_.push_back((uint16_t)0);

                // ending value for clustering key range
                db_query->cr.finish_.push_back((uint16_t)0xffff);
                db_query->cr.finish_.push_back((uint16_t)0xffff);
            }
            {
                db_query_client->cfname = g_viz_constants.SESSION_TABLE;
                db_query_client->row_key_suffix.push_back((uint8_t)is_si);
                db_query_client->row_key_suffix.push_back(
                                (uint8_t)SessionType::CLIENT_SESSION);
                // starting value for clustering key range
                db_query_client->cr.start_.push_back((uint16_t)0);

                // ending value for clustering key range
                db_query_client->cr.finish_.push_back((uint16_t)0xffff);
                db_query_client->cr.finish_.push_back((uint16_t)0xffff);

            }
        } else if (m_query->is_session_query(m_query->table())) {

            db_query->row_key_suffix.push_back((uint8_t)is_si);
            db_query->row_key_suffix.push_back((uint8_t)session_type);
            db_query->cfname = g_viz_constants.SESSION_TABLE;

            // starting value for clustering key range
            db_query->cr.start_.push_back((uint16_t)0);

            // ending value for clustering key range
            db_query->cr.finish_.push_back((uint16_t)0xffff);
            db_query->cr.finish_.push_back((uint16_t)0xffff);

        } else if (m_query->is_object_table_query(m_query->table())) {
            db_query->cfname = g_viz_constants.COLLECTOR_GLOBAL_TABLE;
            db_query->t_only_col = true;
            db_query->t_only_row = true;
            bool object_id_specified = false;

            // handling where * for object table is similar to 
            // and subset of object-id=X handling
            handle_object_type_value(m_query, db_query, object_id_specified);
            QE_TRACE(DEBUG, "where * for object table" << m_query->table());

        }
        // This is "where *" query, no need to do JSON parsing
        return;
    }

    // Do JSON parsing
    contrail_rapidjson::Document d;
    std::string json_string = "{ \"where\" : " + 
        where_json_string + " }";

    QE_TRACE(DEBUG, "where query:" << json_string);
    d.Parse<0>(const_cast<char *>(json_string.c_str()));
    const contrail_rapidjson::Value& json_or_list = d["where"]; 
    QE_PARSE_ERROR(json_or_list.IsArray());

    QE_TRACE(DEBUG, "number of OR terms in where :" << json_or_list.Size());

    if (or_number == -1) wterms_ = json_or_list.Size();

    for (contrail_rapidjson::SizeType i = 0; i < json_or_list.Size(); i++) 
    {
        const contrail_rapidjson::Value& json_or_node = json_or_list[i];
        QE_PARSE_ERROR(json_or_list[i].IsArray());
        QE_INVALIDARG_ERROR(json_or_list[i].Size() != 0);

        // If the or_number is -1, we are in query prepare.
        // We have no intention of actually executing the query.
        // But, we parse everything to catch errors.
        if (or_number != -1) {
            // Only execute the requested OR term
            if (or_number != (int)i) continue;
        }

        QE_TRACE(DEBUG, "number of AND term in " << (i+1) << 
                "th OR term is " <<json_or_node.Size());

        // these are needed because flow index table queries
        // span multiple WHERE match component
        bool vr_match = false; GenDb::DbDataValue vr, vr2; int vr_op = 0;
        bool svn_match = false; GenDb::DbDataValue svn, svn2; int svn_op = 0;
        bool dvn_match = false; GenDb::DbDataValue dvn, dvn2; int dvn_op = 0;
        bool sip_match = false; GenDb::DbDataValue sip, sip2; int sip_op = 0;
        bool dip_match = false; GenDb::DbDataValue dip, dip2; int dip_op = 0;
        bool proto_match = false; GenDb::DbDataValue proto, proto2; int proto_op = 0;
        bool sport_match = false; GenDb::DbDataValue sport, sport2; int sport_op = 0;
        bool dport_match = false; GenDb::DbDataValue dport, dport2; int dport_op = 0;
        bool name_match = false; GenDb::DbDataValue sname_val; int name_op = 0;
        bool object_id_specified = false;
        bool isSession = m_query->is_session_query(m_query->table());
        GenDb::WhereIndexInfoVec labels_vec, remote_labels_vec;
        GenDb::WhereIndexInfoVec custom_tags_vec, remote_custom_tags_vec;
        GenDb::WhereIndexInfoVec where_vec_session_rest, where_vec_stats;
        std::vector<GenDb::WhereIndexInfoVec> where_vec_tags_stats(4);
        std::vector<filter_match_t> filter_and;

        // All where parameters in subquery are AND.
        // So they are in the same msg_table_db_query object.
        // If there are no where-params, this would result in no-op.
        DbQueryUnit *msg_table_db_query = NULL;
        if (m_query->is_message_table_query() ||
            m_query->is_object_table_query(m_query->table())) {

            msg_table_db_query = new DbQueryUnit(this, main_query);
            msg_table_db_query->cfname = g_viz_constants.COLLECTOR_GLOBAL_TABLE;
            msg_table_db_query->t_only_row = true;
            msg_table_db_query->t_only_col = true;
        }

        for (contrail_rapidjson::SizeType j = 0; j < json_or_node.Size(); j++)
        {
            QE_PARSE_ERROR((json_or_node[j].HasMember(WHERE_MATCH_NAME) &&
                json_or_node[j].HasMember(WHERE_MATCH_VALUE) &&
                json_or_node[j].HasMember(WHERE_MATCH_OP)));
            const contrail_rapidjson::Value& name_value =
                json_or_node[j][WHERE_MATCH_NAME];
            const contrail_rapidjson::Value&  value_value =
                json_or_node[j][WHERE_MATCH_VALUE];
            const contrail_rapidjson::Value& op_value =
                json_or_node[j][WHERE_MATCH_OP];

            // do some validation checks
            QE_INVALIDARG_ERROR(name_value.IsString());
            QE_INVALIDARG_ERROR
                ((value_value.IsString() || value_value.IsNumber()));
            QE_INVALIDARG_ERROR(op_value.IsNumber());

            std::string name = name_value.GetString();
            QE_INVALIDARG_ERROR(m_query->is_valid_where_field(name));

            // extract value after type conversion
            std::string value;
            {
                if (value_value.IsString())
                {
                    value = value_value.GetString();
                } else if (value_value.IsInt()){
                    int int_value;
                    std::ostringstream convert;
                    int_value = value_value.GetInt();
                    convert << int_value;
                    value = convert.str();
                } else if (value_value.IsUint()) {
                    uint32_t uint_value;
                    std::ostringstream convert;
                    uint_value = value_value.GetUint();
                    convert << uint_value;
                    value = convert.str();
                } else if (value_value.IsDouble()) {
                    double dbl_value;
                    std::ostringstream convert;
                    dbl_value = value_value.GetDouble();
                    convert << dbl_value;
                    value = convert.str();
                }
            }

            match_op op = (match_op)op_value.GetInt();

            name = get_column_name(name); // Get actual Cassandra name
           
            // this is for range queries
            std::string value2;
            if (op == IN_RANGE)
            {
                QE_PARSE_ERROR(json_or_node[j].HasMember(WHERE_MATCH_VALUE2));
                const contrail_rapidjson::Value&  value_value2 =
                json_or_node[j][WHERE_MATCH_VALUE2];

                // extract value2 after type conversion
                if (value_value2.IsString())
                {
                    value2 = value_value2.GetString();
                } else if (value_value2.IsInt()){
                    int int_value;
                    std::ostringstream convert;
                    int_value = value_value2.GetInt();
                    convert << int_value;
                    value2 = convert.str();
                } else if (value_value2.IsUint()) {
                    uint32_t uint_value;
                    std::ostringstream convert;
                    uint_value = value_value2.GetUint();
                    convert << uint_value;
                    value2 = convert.str();
                } else if (value_value2.IsDouble()) {
                    double dbl_value;
                    std::ostringstream convert;
                    dbl_value = value_value2.GetDouble();
                    convert << dbl_value;
                    value2 = convert.str();
                }
            }

            bool isStat = m_query->is_stat_table_query(m_query->table());
            if ((name == g_viz_constants.SOURCE) && (!isStat))
            {
                QE_INVALIDARG_ERROR((op == EQUAL) || (op == PREFIX));
                QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                    &(msg_table_db_query->where_vec), name,
                    get_gendb_op_from_op(op), value));
                QE_TRACE(DEBUG, "where match term for source " << value);
            }


            if ((name == g_viz_constants.MODULE) && (!isStat))
            {
                QE_INVALIDARG_ERROR((op == EQUAL) || (op == PREFIX));
                QE_INVALIDARG_ERROR(populate_where_vec(m_query, 
                    &(msg_table_db_query->where_vec), name,
                    get_gendb_op_from_op(op), value));

                // dont filter query engine logs if the query is about query
                // engine
                if (value == m_query->sandesh_moduleid)
                    m_query->filter_qe_logs = false;

                QE_TRACE(DEBUG, "where match term for module " << value);
            }

            if ((name == g_viz_constants.MESSAGE_TYPE) && (!isStat))
            {
                QE_INVALIDARG_ERROR((op == EQUAL) || (op == PREFIX));
                QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                    &(msg_table_db_query->where_vec), name,
                    get_gendb_op_from_op(op), value));
                QE_TRACE(DEBUG, "where match term for msg-type " << value);
            }

            if (name == OBJECTID)
            {
                QE_INVALIDARG_ERROR((op == EQUAL) || (op == PREFIX));

                // Object-id is saved in column[6..11] in MessageTablev2 in the format
                // T2:ObjectType:ObjectId
                // T2: is prefixed later, we need to prefix ObjectType: here.
                std::string val = m_query->table() + ":" + value;
                std::string col_name = g_viz_constants.OBJECT_TYPE_NAME1;
                QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                    &(msg_table_db_query->where_vec),
                    col_name, get_gendb_op_from_op(op), val));
                object_id_specified = true;
                QE_TRACE(DEBUG, "where match term for objectid " << value);
            }

            if (m_query->is_session_query(m_query->table())) {
                if (name == g_viz_constants.SessionRecordNames[
                                SessionRecordFields::SESSION_PROTOCOL])
                {
                    proto_match = true; proto_op = op;
                    uint16_t proto_value, proto_value2;
                    std::istringstream(value) >> proto_value;
                    proto = proto_value;
                    if (proto_op == IN_RANGE)
                    {
                        std::istringstream(value2) >> proto_value2;
                        proto2 = proto_value2;
                    } else {
                        QE_INVALIDARG_ERROR(proto_op == EQUAL);
                    }
                    QE_TRACE(DEBUG, "where match term for proto_value " << value);
                }
                else if (name == g_viz_constants.SessionRecordNames[
                                SessionRecordFields::SESSION_SPORT])
                {
                    sport_match = true; sport_op = op;
                    uint16_t sport_value, sport_value2;
                    std::istringstream(value) >> sport_value;
                    sport = sport_value;
                    if (sport_op == IN_RANGE)
                    {
                        std::istringstream(value2) >> sport_value2;
                        sport2 = sport_value2;
                    } else {
                        QE_INVALIDARG_ERROR(sport_op == EQUAL);
                    }
                    QE_TRACE(DEBUG, "where match term for sport_value " << value);
                } else if (name == g_viz_constants.SessionRecordNames[
                                 SessionRecordFields::SESSION_LABELS]) {
                    QE_INVALIDARG_ERROR(op == CONTAINS);
                    value = "%" + value;
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &labels_vec, name, GenDb::Op::LIKE, value));
                } else if (name == g_viz_constants.SessionRecordNames[
                                 SessionRecordFields::SESSION_REMOTE_LABELS]) {
                    QE_INVALIDARG_ERROR(op == CONTAINS);
                    value = "%" + value;
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &remote_labels_vec, name, GenDb::Op::LIKE, value));
                } else if (name == g_viz_constants.SessionRecordNames[
                                 SessionRecordFields::SESSION_CUSTOM_TAGS]) {
                    QE_INVALIDARG_ERROR(op == CONTAINS);
                    value = "%" + value;
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &custom_tags_vec, name, GenDb::Op::LIKE, value));
                } else if (name == g_viz_constants.SessionRecordNames[
                                 SessionRecordFields::SESSION_REMOTE_CUSTOM_TAGS]) {
                    QE_INVALIDARG_ERROR(op == CONTAINS);
                    value = "%" + value;
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &remote_custom_tags_vec, name, GenDb::Op::LIKE, value));
                } else {
                    GenDb::Op::type comparator;
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &where_vec_session_rest, name, comparator, value));
                }
            } else if (m_query->is_flow_query(m_query->table())){
                if (name == g_viz_constants.FlowRecordNames[
                                FlowRecordFields::FLOWREC_PROTOCOL])
                {
                    proto_match = true; proto_op = op;
                    uint16_t proto_value, proto_value2;
                    std::istringstream(value) >> proto_value;
                    proto = proto_value;
                    if (proto_op == IN_RANGE)
                    {
                        std::istringstream(value2) >> proto_value2;
                        proto2 = proto_value2;
                    } else {
                        QE_INVALIDARG_ERROR(proto_op == EQUAL);
                    }
                    QE_TRACE(DEBUG, "where match term for proto_value " << value);
                }
                if (name == g_viz_constants.FlowRecordNames[
                                FlowRecordFields::FLOWREC_SOURCEVN])
                {
                    svn_match = true; svn_op = op;
                    svn = value;
                    QE_INVALIDARG_ERROR((svn_op == EQUAL)||(svn_op == PREFIX));

                    QE_TRACE(DEBUG, "where match term for sourcevn " << value);
                }
                if (name == g_viz_constants.FlowRecordNames[
                                FlowRecordFields::FLOWREC_DESTVN])
                {
                    dvn_match = true; dvn_op = op;
                    dvn = value;
                    QE_INVALIDARG_ERROR((dvn_op == EQUAL)||(dvn_op == PREFIX));

                    QE_TRACE(DEBUG, "where match term for sourcevn " << value);
                }
                if (name == g_viz_constants.FlowRecordNames[
                                FlowRecordFields::FLOWREC_SOURCEIP])
                {
                    sip_match = true; sip_op = op;
                    sip = value;
                    QE_TRACE(DEBUG, "where match term for sourceip " << value);
                    if (sip_op == IN_RANGE)
                    {
                        sip2 = value2;
                    } else {
                        QE_INVALIDARG_ERROR(sip_op == EQUAL);
                    }
                    if (direction_ing == 0) {
                        filter_match_t filter;
                        filter.name = "sourceip";
                        filter.op = (match_op)sip_op;
                        filter.value = boost::get<std::string>(sip);
                        filter_and.push_back(filter);
                        additional_select_.push_back(filter.name);
                    }
                }
                if (name == g_viz_constants.FlowRecordNames[
                                FlowRecordFields::FLOWREC_DESTIP])
                {
                    dip_match = true; dip_op = op;
                    dip = value;
                    QE_TRACE(DEBUG, "where match term for destip " << value);
                    if (dip_op == IN_RANGE)
                    {
                        dip2 = value2;
                    } else {
                        QE_INVALIDARG_ERROR(dip_op == EQUAL);
                    }
                    if (direction_ing == 1) {
                        filter_match_t filter;
                        filter.name = "destip";
                        filter.op = (match_op)dip_op;
                        filter.value = boost::get<std::string>(dip);
                        filter_and.push_back(filter);
                        additional_select_.push_back(filter.name);
                    }
                }
                if (name == g_viz_constants.FlowRecordNames[FlowRecordFields::FLOWREC_SPORT])
                {
                    sport_match = true; sport_op = op;

                    uint16_t sport_value;
                    std::istringstream(value) >> sport_value;

                    sport = sport_value;
                    if (sport_op == IN_RANGE)
                    {
                        uint16_t sport_value2;
                        std::istringstream(value2) >> sport_value2;
                        sport2 = sport_value2;
                    } else {
                        QE_INVALIDARG_ERROR(sport_op == EQUAL);
                    }

                    filter_match_t filter;
                    filter.name = "sport";
                    filter.op = (match_op)sport_op;
                    std::ostringstream convert;
                    convert << boost::get<uint16_t>(sport);
                    filter.value = convert.str();
                    filter_and.push_back(filter);
                    additional_select_.push_back(filter.name);

                    QE_TRACE(DEBUG, "where match term for sport " << value);
                }
                if (name == g_viz_constants.FlowRecordNames[FlowRecordFields::FLOWREC_DPORT])
                {
                    dport_match = true; dport_op = op;

                    uint16_t dport_value;
                    std::istringstream(value) >> dport_value;
                    dport = dport_value;
                    if (dport_op == IN_RANGE)
                    {
                        uint16_t dport_value2;
                        std::istringstream(value2) >> dport_value2;
                        dport2 = dport_value2;
                    } else {
                        QE_INVALIDARG_ERROR(dport_op == EQUAL);
                    }

                    filter_match_t filter;
                    filter.name = "dport";
                    filter.op = (match_op)dport_op;
                    std::ostringstream convert;
                    convert << boost::get<uint16_t>(dport);
                    filter.value = convert.str();
                    filter_and.push_back(filter);
                    additional_select_.push_back(filter.name);

                    QE_TRACE(DEBUG, "where match term for dport " << value);
                }
                if (name == g_viz_constants.FlowRecordNames[FlowRecordFields::FLOWREC_VROUTER])
                {
                    vr_match = true;
                    vr_op = op;
                    vr = value;
                    QE_INVALIDARG_ERROR((vr_op == EQUAL)||(vr_op == PREFIX));

                    QE_TRACE(DEBUG, "where match term for vrouter " << value);
                    filter_match_t filter;
                    filter.name = "vrouter";
                    if (vr_op != PREFIX) {
                        filter.op = (match_op)vr_op;
                    } else {
                        filter.op = REGEX_MATCH;
                    }
                    filter.value = boost::get<std::string>(vr);
                    if (filter.op == REGEX_MATCH) {
                        filter.match_e = regex(filter.value);
                    }
                    if (vr_match) {
                    }
                    filter_and.push_back(filter);
                    additional_select_.push_back(filter.name);
                }
            }
            if (isStat)
            {   
                if (oldDataExists) {
                    // Call StatTermProcess to handle the query into older tables
                    StatTermProcess(json_or_node[j], this, main_query);
                }

                std::string pname, sname;
                match_op pop,sop;
                GenDb::DbDataValue pval, pval2, sval, sval2;

                if (!StatTermParse(main_query, json_or_node[j],
                    pname, pop, pval, pval2, sname, sop, sval, sval2)) {
                    QE_INVALIDARG_ERROR(false);
                }

                if (pname == g_viz_constants.STATS_NAME_FIELD) {
                    name_match = true;
                    sname_val = pval;
                    name_op = pop;
                } else if (pname == g_viz_constants.STATS_SOURCE_FIELD ||
                    boost::algorithm::ends_with(pname, g_viz_constants.STATS_KEY_FIELD) ||
                    boost::algorithm::ends_with(pname, g_viz_constants.STATS_PROXY_FIELD)) {
                    if (boost::algorithm::ends_with(pname, g_viz_constants.STATS_KEY_FIELD)) {
                        pname = g_viz_constants.STATS_KEY_FIELD;
                    }
                    if (boost::algorithm::ends_with(pname, g_viz_constants.STATS_PROXY_FIELD)) {
                        pname = g_viz_constants.STATS_PROXY_FIELD;
                    }
                    QE_INVALIDARG_ERROR(pop == EQUAL || pop == PREFIX);
                    GenDb::Op::type db_op;
                    if (pop == EQUAL) {
                        db_op = GenDb::Op::EQ;
                        QE_INVALIDARG_ERROR(populate_where_vec(m_query, &where_vec_stats,
                            pname, db_op, GenDb::DbDataValueToString(pval)));
                    } else {
                        std::string val(GenDb::DbDataValueToString(pval));
                        if (!val.empty()) {
                            db_op = GenDb::Op::LIKE;
                            QE_INVALIDARG_ERROR(populate_where_vec(m_query, &where_vec_stats,
                                pname, db_op, val));
                        }
                    }
                } else {
                    GenDb::Op::type db_op;
                    pval = "%" + pname + "=" + GenDb::DbDataValueToString(pval);
                    db_op = GenDb::Op::LIKE;
                    size_t idx = djb_hash(pname.c_str(), pname.length())
                        % g_viz_constants.NUM_STATS_TAGS_FIELD;
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query, &where_vec_tags_stats[idx],
                        g_viz_constants.STATS_TAGS_FIELD + integerToString(idx),
                        db_op, GenDb::DbDataValueToString(pval)));
                }
                if (sop != 0) {
                    if (sname == g_viz_constants.STATS_NAME_FIELD) {
                        name_match = true;
                        sname_val = sval;
                        name_op = sop;
                    } else if (sname == g_viz_constants.STATS_SOURCE_FIELD ||
                        boost::algorithm::ends_with(pname, g_viz_constants.STATS_KEY_FIELD) ||
                        boost::algorithm::ends_with(pname, g_viz_constants.STATS_PROXY_FIELD)) {
                        if (boost::algorithm::ends_with(pname, g_viz_constants.STATS_KEY_FIELD)) {
                            pname = g_viz_constants.STATS_KEY_FIELD;
                        }
                        if (boost::algorithm::ends_with(pname, g_viz_constants.STATS_PROXY_FIELD)) {
                            pname = g_viz_constants.STATS_PROXY_FIELD;
                        }

                        QE_INVALIDARG_ERROR(sop == EQUAL || sop == PREFIX);
                        GenDb::Op::type db_op;
                        if (sop == EQUAL) {
                            db_op = GenDb::Op::EQ;
                        } else {
                            db_op = GenDb::Op::LIKE;
                        }
                        QE_INVALIDARG_ERROR(populate_where_vec(m_query, &where_vec_stats,
                            sname, db_op, GenDb::DbDataValueToString(sval)));
                    } else {
                        QE_INVALIDARG_ERROR(sop == EQUAL || sop == PREFIX);
                        GenDb::Op::type db_op;
                        sval = "%" + sname + "=" + GenDb::DbDataValueToString(sval);
                        db_op = GenDb::Op::LIKE;
                        size_t idx = djb_hash(sname.c_str(), sname.length())
                            % g_viz_constants.NUM_STATS_TAGS_FIELD;
                        QE_INVALIDARG_ERROR(populate_where_vec(m_query, &where_vec_tags_stats[idx], 
                            g_viz_constants.STATS_TAGS_FIELD + integerToString(idx),
                            db_op, GenDb::DbDataValueToString(sval)));
                    }
                }
                object_id_specified = true;
            }
        }

        if (m_query->is_stat_table_query(m_query->table())) {
            std::vector<GenDb::WhereIndexInfoVec> where_vec_list;
            populate_stats_where_vec_list(&where_vec_list, where_vec_stats,
                where_vec_tags_stats);
            BOOST_FOREACH(const GenDb::WhereIndexInfoVec &where_vec, where_vec_list) {
                DbQueryUnit *db_query = new DbQueryUnit(this, main_query);
                db_query->cfname = g_viz_constants.STATS_TABLE;
                std::string tstr, astr;
                GetStatTableAttrName(m_query->table(), &tstr, &astr);
                db_query->row_key_suffix.push_back(tstr);
                db_query->row_key_suffix.push_back(astr);
                db_query->where_vec = where_vec;
                if (name_match) {
                    db_query->cr.start_.push_back(sname_val);
                    if (name_op == EQUAL) {
                        db_query->cr.finish_.push_back(sname_val);
                    } else if (name_op == PREFIX) {
                        db_query->cr.finish_.push_back(
                            GenDb::DbDataValueToString(sname_val) + "\x7f");
                    } else {
                        QE_INVALIDARG_ERROR(false);
                    }
                } else {
                    db_query->cr.start_.push_back("\x00");
                    db_query->cr.finish_.push_back("\x7f");
                }
            }
        }

        // common handling similar to object table where * case
        if (m_query->is_message_table_query() ||
            m_query->is_object_table_query(m_query->table())) {
            handle_object_type_value(m_query, msg_table_db_query,
                                     object_id_specified);
        }

        if (isSession) {
            std::vector<GenDb::WhereIndexInfoVec> where_vec_list;
            populate_session_where_vec_list(&where_vec_list, where_vec_session_rest, labels_vec, remote_labels_vec,
                custom_tags_vec, remote_custom_tags_vec);

            BOOST_FOREACH(const GenDb::WhereIndexInfoVec &where_vec, where_vec_list) {
                DbQueryUnit *session_db_query = new DbQueryUnit(this, main_query);
                session_db_query->cfname = g_viz_constants.SESSION_TABLE;
                session_db_query->row_key_suffix.push_back((uint8_t)is_si);
                session_db_query->row_key_suffix.push_back((uint8_t)session_type);
                session_db_query->where_vec = where_vec;

                if (proto_match) {
                    session_db_query->cr.start_.push_back(proto);
                    if (proto_op == EQUAL) {
                        session_db_query->cr.finish_.push_back(proto);
                    } else if (proto_op == IN_RANGE) {
                        session_db_query->cr.finish_.push_back(proto2);
                    }
                } else {
                    session_db_query->cr.start_.push_back((uint16_t)0);
                    session_db_query->cr.finish_.push_back((uint16_t)0xffff);
                }
                if (sport_match) {
                    QE_INVALIDARG_ERROR(proto_match);
                    session_db_query->cr.start_.push_back(sport);
                    if(sport_op == EQUAL) {
                        session_db_query->cr.finish_.push_back(sport);
                    } else if (sport_op == IN_RANGE) {
                        session_db_query->cr.finish_.push_back(sport2);
                    }
                } else {
                    session_db_query->cr.finish_.push_back((uint16_t)0xffff);
                }
            }
        }
        else if (m_query->is_flow_query(m_query->table())) {
            if (!filter_and.empty()) {
                filter_list_.push_back(filter_and);
            }
            {
                DbQueryUnit *client_session_query = new DbQueryUnit(this, main_query);
                client_session_query->cfname = g_viz_constants.SESSION_TABLE;
                client_session_query->row_key_suffix.push_back(
                                        (uint8_t)SessionType::CLIENT_SESSION);
                if (proto_match) {
                    client_session_query->cr.start_.push_back(proto);
                    if (proto_op == EQUAL) {
                        client_session_query->cr.finish_.push_back(proto);
                    } else if (proto_op == IN_RANGE) {
                        client_session_query->cr.finish_.push_back(proto2);
                    }
                } else {
                    client_session_query->cr.start_.push_back(((uint16_t)0));
                    client_session_query->cr.finish_.push_back(((uint16_t)0xffff));
                }
                if ((direction_ing == 0 && sport_match) ||
                    (direction_ing == 1 && dport_match)) {
                    QE_INVALIDARG_ERROR(proto_match);
                    client_session_query->cr.start_.push_back(direction_ing?
                            dport:sport);
                    int op = direction_ing?dport_op:sport_op;
                    if (op == EQUAL) {
                        client_session_query->cr.finish_.push_back(direction_ing?
                            dport:sport);
                    } else if (op == IN_RANGE) {
                        client_session_query->cr.finish_.push_back(direction_ing?
                            dport2:sport2);
                    }
                } else {
                    client_session_query->cr.finish_.push_back((uint16_t)0xffff);
                }
                if ((direction_ing == 0 && dip_match) ||
                    (direction_ing == 1 && sip_match)) {

                    int op = direction_ing?sip_op:dip_op;
                    std::string val = direction_ing?
                        (boost::get<std::string>(sip)):(boost::get<std::string>(dip));
                    GenDb::Op::type comparator;
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(client_session_query->where_vec), "local_ip", comparator, val));
                }
                if ((direction_ing == 0 && dvn_match) ||
                    (direction_ing == 1 && svn_match)) {
                    int op = direction_ing?svn_op:dvn_op;
                    std::string val = direction_ing?
                        (boost::get<std::string>(svn)):(boost::get<std::string>(dvn));
                    GenDb::Op::type comparator;
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(client_session_query->where_vec), "vn", comparator, val));
                }
                if ((direction_ing == 0 && svn_match) ||
                    (direction_ing == 1 && dvn_match)) {
                    int op = (direction_ing?dvn_op:svn_op);
                    GenDb::Op::type comparator;
                    std::string val = direction_ing?
                        (boost::get<std::string>(dvn)):(boost::get<std::string>(svn));
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(client_session_query->where_vec), "remote_vn", comparator, val));
                }
            }
            {
                DbQueryUnit *server_session_query = new DbQueryUnit(this, main_query);
                server_session_query->cfname = g_viz_constants.SESSION_TABLE;
                server_session_query->row_key_suffix.push_back(
                                        (uint8_t)SessionType::SERVER_SESSION);
                if (proto_match) {
                    server_session_query->cr.start_.push_back(proto);
                    if(proto_op == EQUAL) {
                        server_session_query->cr.finish_.push_back(proto);
                    }
                    else if (proto_op == IN_RANGE) {
                        server_session_query->cr.finish_.push_back(proto2);
                    }
                } else {
                    server_session_query->cr.start_.push_back(((uint16_t)0));
                    server_session_query->cr.finish_.push_back(((uint16_t)0xffff));
                }
                if ((direction_ing == 0 && dport_match) ||
                    (direction_ing == 1 && sport_match)) {
                    QE_INVALIDARG_ERROR(proto_match);
                    server_session_query->cr.start_.push_back(direction_ing?
                            sport:dport);
                    int op = direction_ing?sport_op:dport_op;
                    if(op == EQUAL) {
                        server_session_query->cr.finish_.push_back(direction_ing?
                            sport:dport);
                    } else if (op == IN_RANGE) {
                        server_session_query->cr.finish_.push_back(direction_ing?
                            sport2:dport2);
                    }
                } else {
                    server_session_query->cr.finish_.push_back((uint16_t)0xffff);
                }
                if ((direction_ing == 0 && dip_match) ||
                    (direction_ing == 1 && sip_match)) {
                    int op = direction_ing?sip_op:dip_op;
                    std::string val = direction_ing?
                        (boost::get<std::string>(sip)):(boost::get<std::string>(dip));
                    GenDb::Op::type comparator;
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(server_session_query->where_vec), "local_ip", comparator, val));
                }
                if ((direction_ing == 0 && dvn_match) ||
                    (direction_ing == 1 && svn_match)) {
                    int op = (direction_ing?svn_op:dvn_op);
                    std::string val = direction_ing?
                        (boost::get<std::string>(svn)):(boost::get<std::string>(dvn));
                    GenDb::Op::type comparator;
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(server_session_query->where_vec), "vn", comparator, val));
                }
                if ((direction_ing == 0 && svn_match) ||
                    (direction_ing == 1 && dvn_match)) {
                    int op = (direction_ing?dvn_op:svn_op);
                    GenDb::Op::type comparator;
                    std::string val = direction_ing?
                        (boost::get<std::string>(dvn)):(boost::get<std::string>(svn));
                    if (op == PREFIX) {
                        comparator = GenDb::Op::LIKE;
                    } else {
                        comparator = GenDb::Op::EQ;
                    }
                    QE_INVALIDARG_ERROR(populate_where_vec(m_query,
                        &(server_session_query->where_vec), "remote_vn", comparator, val));
                }
            }
        }
    }
}

// For UT
WhereQuery::WhereQuery(QueryUnit *mq): QueryUnit(mq, mq){
}

void WhereQuery::populate_session_where_vec_list(std::vector<GenDb::WhereIndexInfoVec> *where_vec_list,
    const GenDb::WhereIndexInfoVec &rest_where_vec,
    const GenDb::WhereIndexInfoVec &labels_vec,
    const GenDb::WhereIndexInfoVec &remote_labels_vec,
    const GenDb::WhereIndexInfoVec &custom_tags_vec,
    const GenDb::WhereIndexInfoVec &remote_custom_tags_vec) {

    uint16_t max_random_attr = std::max(std::max(std::max(labels_vec.size(),
        remote_labels_vec.size()), custom_tags_vec.size()),
        remote_custom_tags_vec.size());
    if (max_random_attr == 0) {
        where_vec_list->push_back(rest_where_vec);
    } else  {
        for (size_t i = 0; i < max_random_attr; ++i) {
            GenDb::WhereIndexInfoVec where_vec(rest_where_vec);
            if (i < labels_vec.size()) {
                where_vec.push_back(labels_vec[i]);
            }
            if (i < remote_labels_vec.size()) {
                where_vec.push_back(remote_labels_vec[i]);
            }
            if (i < custom_tags_vec.size()) {
                where_vec.push_back(custom_tags_vec[i]);
            }
            if (i < remote_custom_tags_vec.size()) {
                where_vec.push_back(remote_custom_tags_vec[i]);
            }
            where_vec_list->push_back(where_vec);
        }
    }
}

void WhereQuery::subquery_processed(QueryUnit *subquery) {
    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;
    {
        tbb::mutex::scoped_lock lock(vector_push_mutex_);
        int sub_query_id = ((DbQueryUnit *)subquery)->sub_query_id;
        if (((DbQueryUnit *)subquery)->cfname == g_viz_constants.OBJECT_TABLE) {
            inp.insert(inp.begin(), sub_queries[sub_query_id]->query_result.get());
        } else if (((DbQueryUnit *)subquery)->cfname == g_viz_constants.STATS_TABLE) {
            inp_new_data.push_back((sub_queries[sub_query_id]->query_result.get()));
        } else {
            inp.push_back((sub_queries[sub_query_id]->query_result.get()));
        }
        if (subquery->query_status == QUERY_FAILURE) {
            QE_QUERY_FETCH_ERROR();
        }
        if (sub_queries.size() != inp.size() + inp_new_data.size()) {
            return;
        }
    }

    // Handle if any of the sub query has failed.
    if (m_query->qperf_.error) {
        m_query->qperf_.chunk_where_time =
        static_cast<uint32_t>((UTCTimestampUsec() - m_query->where_start_)
        /1000);
        where_query_cb_(m_query->handle_, m_query->qperf_, std::auto_ptr<std::vector<query_result_unit_t>>(where_result_.release()));
        return;
    }
    if (m_query->is_message_table_query()
        || m_query->is_object_table_query(m_query->table())
        || m_query->is_flow_query(m_query->table())
        ) {
        SetOperationUnit::op_or(((AnalyticsQuery *)(this->main_query))->query_id,
            *where_result_, inp);
    } else if (m_query->is_stat_table_query(m_query->table())) {
        std::vector<WhereResultT*> inp_final;
        if (inp.size() != 0) {
            std::unique_ptr<WhereResultT>
                where_result_old(new std::vector<query_result_unit_t>);
            SetOperationUnit::op_and(((AnalyticsQuery *)(this->main_query))->query_id,
                *where_result_old, inp);
            inp_final.push_back(where_result_old.get());
        }
        std::unique_ptr<WhereResultT>
            where_result_new(new std::vector<query_result_unit_t>);
        SetOperationUnit::op_and(((AnalyticsQuery *)(this->main_query))->query_id,
            *where_result_new, inp_new_data);
        inp_final.push_back(where_result_new.get());
        SetOperationUnit::op_or(((AnalyticsQuery *)(this->main_query))->query_id,
            *where_result_, inp_final);
    } else {
        SetOperationUnit::op_and(((AnalyticsQuery *)(this->main_query))->query_id,
            *where_result_, inp);
    }
    m_query->query_status = query_status;

    QE_TRACE(DEBUG, "Set ops returns # of rows:" << where_result_->size());

    // Have the result ready and processing is done
    QE_TRACE(DEBUG, "WHERE processing done row #s:" <<
         where_result_->size());
    QE_TRACE_NOQID(DEBUG, " Finished where processing for QID " << m_query->query_id
        << " chunk:" << m_query->parallel_batch_num);
    status_details = 0;
    parent_query->subquery_processed(this);
    m_query->status_details = status_details;
    m_query->qperf_.chunk_where_time =
        static_cast<uint32_t>((UTCTimestampUsec() - m_query->where_start_)
        /1000);
    where_query_cb_(m_query->handle_, m_query->qperf_,std::auto_ptr<std::vector<query_result_unit_t>>(where_result_.release()));
}

query_status_t WhereQuery::process_query()
{
    AnalyticsQuery *m_query = (AnalyticsQuery *)main_query;

    if (status_details != 0)
    {
        QE_TRACE(DEBUG, 
             "No need to process query, as there were errors previously");
        return QUERY_FAILURE;
    }

    QE_TRACE(DEBUG, "WhereQuery" );

    QE_TRACE(DEBUG, "Starting processing of " << sub_queries.size() <<
            " subqueries");

    if (m_query->table() == g_viz_constants.OBJECT_VALUE_TABLE) {
        status_details = 0;
        parent_query->subquery_processed(this);
        return QUERY_SUCCESS;
    }
    unsigned int v_size = sub_queries.size();
    // invoke processing of all the sub queries
    // TBD: Handle ASYNC processing
    for (unsigned int i = 0; i < v_size; i++)
    {
        query_status = sub_queries[i]->process_query();
        if (query_status == QUERY_FAILURE) {
            return query_status;
        }
    }
    return query_status;
}

// We need to cover 2 cases here in MessageTablev2
// (a) --object-type is specified without any --object-id
// (b) --object-type and --object-id are specified

// (a) ObjectTypeValue fields are stored in following format
//  T2:ObjectType:ObjectId
//  We need to query for T2:ObjectType*
// (b) We have 6 columns to save OBJECTID.
// Any OBJECTID could be in any of the 6 columns.
// For OBJECTID query, we need to check each of the 6 columns.
// Since its an OR operation, we need to create 6 queries, one
// for each column.
// Combining (a) & (b) we end up creating 6 queries 1 for each
// ObjectTypeValue[1..6] column.
void WhereQuery::handle_object_type_value(
                                    AnalyticsQuery *m_query,
                                    DbQueryUnit *db_query,
                                    bool object_id_specified)
{
    if (m_query->is_object_table_query(m_query->table())) {
        QE_TRACE(DEBUG, "object-type-value handling");
        std::string column1 = query_column_to_cass_column(m_query,
                                        g_viz_constants.OBJECT_TYPE_NAME1);
        if (column1.empty()) {
            QE_INVALIDARG_ERROR(false);
        }
        if (object_id_specified == false) {
            // create db_query entry for OBJECT_TYPE_NAME1
            // as done for OBJECTID case above.
            // rest falls in place as with --object-id case.
            match_op op = PREFIX;
            std::string val(m_query->table() + ":");
            std::string col_name = g_viz_constants.OBJECT_TYPE_NAME1;
            QE_INVALIDARG_ERROR(populate_where_vec(m_query, &(db_query->where_vec),
                col_name, get_gendb_op_from_op(op), val));
        }

        // regular --object-id processing from here
        int index = 0;
        BOOST_FOREACH(GenDb::WhereIndexInfo &where_info, db_query->where_vec) {
            if (column1 == where_info.get<0>()) {
                break;
            }
            index++;
        }

        // OBJECT_TYPE_NAME1 is already done above
        for (int i = 2;
             i <= g_viz_constants.MSG_TABLE_MAX_OBJECTS_PER_MSG;
             i++) {
            DbQueryUnit *msg_table_db_query2 = new DbQueryUnit(this, main_query);
            msg_table_db_query2->cfname = g_viz_constants.COLLECTOR_GLOBAL_TABLE;
            msg_table_db_query2->t_only_row = true;
            msg_table_db_query2->t_only_col = true;
            msg_table_db_query2->where_vec = db_query->where_vec;

            GenDb::WhereIndexInfo *where_info2 = &msg_table_db_query2->where_vec[index];
            std::string col_name = g_viz_constants.OBJECT_TYPE_NAME_PFX;
            col_name.append(integerToString(i));

            std::string columnN = query_column_to_cass_column(m_query, col_name);
            if (column1.empty()) {
                QE_INVALIDARG_ERROR(false);
            }
            where_info2->get<0>() = columnN;
        }
    }
}

bool WhereQuery::populate_where_vec(AnalyticsQuery *m_query,
                                    GenDb::WhereIndexInfoVec *where_vec,
                                    const std::string& query_col,
                                    const GenDb::Op::type db_op,
                                    const std::string& value) {
    std::string columnN = query_column_to_cass_column(m_query, query_col);
    if (columnN.empty()) {
        return false;
    }
    std::string val(value);
    switch (db_op) {
        case GenDb::Op::LIKE:
        {
            val += "%";
            break;
        }
        default:
            break;
    }
    if (val == "%") {
        return true;
    }
    GenDb::WhereIndexInfo where_info =
            boost::make_tuple(columnN, db_op, val);
    where_vec->push_back(where_info);
    return true;
}

std::string WhereQuery::query_column_to_cass_column(AnalyticsQuery *m_query,
                                                    const std::string& query_column) {
    std::map<std::string, table_schema> schema;
    std::string table_name;
    if (m_query->is_message_table_query(m_query->table()) ||
        m_query->is_object_table_query(m_query->table())) {
        schema = g_viz_constants._VIZD_TABLE_SCHEMA;
        table_name = g_viz_constants.COLLECTOR_GLOBAL_TABLE;
    } else if (m_query->is_session_query(m_query->table()) ||
        m_query->is_flow_query(m_query->table())) {
        schema = g_viz_constants._VIZD_SESSION_TABLE_SCHEMA;
        table_name = g_viz_constants.SESSION_TABLE;
    } else if (m_query->is_stat_table_query(m_query->table())) {
        schema = g_viz_constants._VIZD_STAT_TABLE_SCHEMA;
        table_name = g_viz_constants.STATS_TABLE;
    }
    std::map<std::string, table_schema>::const_iterator it = schema.find(table_name);
    QE_ASSERT(it != schema.end());
    std::map<string, string>::const_iterator itr =
        it->second.index_column_to_column.find(query_column);
    if (itr == (it->second.index_column_to_column.end())) {
        return "";
    }
    return itr->second;
}
