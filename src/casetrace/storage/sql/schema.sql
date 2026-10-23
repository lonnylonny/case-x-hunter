-- CaseTrace 业务 Schema，固定建表文件（M5-01）。
--
-- 权威来源：docs/data/CaseTrace_Data_Structure_V2_No_Scenario.md §5「PostgreSQL Schema 范围」。
-- 本文件只建表、约束和索引，不重新设计 Case 模型，也不写业务数据。
--
-- 执行约定（见 schema.initialize_schema）：
--   * 只用非限定表名；调用方先把会话 search_path 指向 CaseTrace 专用 schema，
--     因此同一份文件可建到开发库、测试库或测试专用 schema，互不影响；
--   * 全部使用 IF NOT EXISTS，重复执行只补缺，不清空、不覆盖已有数据；
--   * schema 版本行由 schema.py 用参数化 INSERT 写入，避免版本号在两处各写一遍。
--
-- 校验分工（结构 §5「校验分工」）：数据库负责主键、外键、组合唯一、枚举、正数量、
-- 日期先后与空白文本等基础约束；跨记录 CR/GR（批号一致性、Case 子记录最少数量、
-- 工序路线并集、同站点分组交集等）继续由 Python 校验在导入事务内执行，不用触发器重写。

-- schema 版本标记：记录当前结构，供初始化时核对。
CREATE TABLE IF NOT EXISTS schema_version (
    version     text PRIMARY KEY CHECK (btrim(version) <> ''),
    description text NOT NULL CHECK (btrim(description) <> ''),
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- 1. 主数据：客户 / 产品族 / 路线 / 产品
-- products.customer_id 直接承接 Excel 客户映射，不再建 customer_product_map 表（结构 §1）。
CREATE TABLE IF NOT EXISTS customers (
    customer_id   text PRIMARY KEY CHECK (btrim(customer_id) <> ''),
    customer_name text NOT NULL CHECK (btrim(customer_name) <> '')
);

CREATE TABLE IF NOT EXISTS product_families (
    product_family_id text PRIMARY KEY CHECK (btrim(product_family_id) <> ''),
    product_family    text NOT NULL CHECK (btrim(product_family) <> '')
);

CREATE TABLE IF NOT EXISTS package_routes (
    package_route     text PRIMARY KEY CHECK (btrim(package_route) <> ''),
    carrier           text NOT NULL CHECK (btrim(carrier) <> ''),
    die_interconnect  text NOT NULL CHECK (btrim(die_interconnect) <> ''),
    external_terminal text NOT NULL CHECK (btrim(external_terminal) <> ''),
    description       text NOT NULL CHECK (btrim(description) <> '')
);

CREATE TABLE IF NOT EXISTS products (
    product_id        text PRIMARY KEY CHECK (btrim(product_id) <> ''),
    product_name      text NOT NULL CHECK (btrim(product_name) <> ''),
    product_family_id text NOT NULL REFERENCES product_families (product_family_id),
    package_route     text NOT NULL REFERENCES package_routes (package_route),
    customer_id       text NOT NULL REFERENCES customers (customer_id),
    product_function  text CHECK (product_function IS NULL OR btrim(product_function) <> '')
);

-- 2. 主数据：Failure Mode 与适用路线
-- applicable_package 拆成 failure_mode_routes 关系表；applicable_process 只是候选知识
-- （结构 §2：不用作匹配门槛或自动填充），保留在本表文本数组列。
CREATE TABLE IF NOT EXISTS failure_modes (
    failure_mode_id      text PRIMARY KEY CHECK (btrim(failure_mode_id) <> ''),
    failure_mode         text NOT NULL CHECK (btrim(failure_mode) <> ''),
    applicable_process   text[] CHECK (applicable_process IS NULL OR array_position(applicable_process, NULL) IS NULL),
    possible_root_causes text CHECK (possible_root_causes IS NULL OR btrim(possible_root_causes) <> ''),
    failure_effects      text CHECK (failure_effects IS NULL OR btrim(failure_effects) <> ''),
    detection_methods    text CHECK (detection_methods IS NULL OR btrim(detection_methods) <> ''),
    screening_methods    text CHECK (screening_methods IS NULL OR btrim(screening_methods) <> ''),
    corrective_actions   text CHECK (corrective_actions IS NULL OR btrim(corrective_actions) <> ''),
    notes                text CHECK (notes IS NULL OR btrim(notes) <> '')
);

CREATE TABLE IF NOT EXISTS failure_mode_routes (
    failure_mode_id text NOT NULL REFERENCES failure_modes (failure_mode_id) ON DELETE CASCADE,
    package_route   text NOT NULL REFERENCES package_routes (package_route),
    PRIMARY KEY (failure_mode_id, package_route)
);

-- 3. 主数据：工序与路线工序映射
-- 工序名称由 processes 查询，package_process_map 不重复保存名称（结构 §2）。
-- incoming / optional / auxiliary 不强制 sequence_no，故该列可空。
CREATE TABLE IF NOT EXISTS processes (
    process_id       text PRIMARY KEY CHECK (btrim(process_id) <> ''),
    process          text NOT NULL CHECK (btrim(process) <> ''),
    process_category text CHECK (process_category IS NULL OR btrim(process_category) <> ''),
    description      text CHECK (description IS NULL OR btrim(description) <> '')
);

CREATE TABLE IF NOT EXISTS package_process_map (
    package_route text NOT NULL REFERENCES package_routes (package_route),
    process_id    text NOT NULL REFERENCES processes (process_id),
    sequence_no   integer,
    relation_type text CHECK (relation_type IS NULL OR btrim(relation_type) <> ''),
    process_scope text CHECK (process_scope IS NULL OR btrim(process_scope) <> ''),
    notes         text CHECK (notes IS NULL OR btrim(notes) <> ''),
    PRIMARY KEY (package_route, process_id)
);

-- 4. Case 与两个无序集合关系表
CREATE TABLE IF NOT EXISTS cases (
    case_id              text PRIMARY KEY CHECK (btrim(case_id) <> ''),
    abnormal_description text NOT NULL CHECK (btrim(abnormal_description) <> ''),
    root_cause           text NOT NULL CHECK (btrim(root_cause) <> ''),
    corrective_action    text NOT NULL CHECK (btrim(corrective_action) <> ''),
    investigation_others text CHECK (investigation_others IS NULL OR btrim(investigation_others) <> '')
);

CREATE TABLE IF NOT EXISTS case_abnormal_processes (
    case_id    text NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    process_id text NOT NULL REFERENCES processes (process_id),
    PRIMARY KEY (case_id, process_id)
);

CREATE TABLE IF NOT EXISTS case_details (
    detail_id       text PRIMARY KEY CHECK (btrim(detail_id) <> ''),
    case_id         text NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    product_id      text NOT NULL REFERENCES products (product_id),
    customer_lot    text NOT NULL CHECK (btrim(customer_lot) <> ''),
    production_lot  text NOT NULL CHECK (btrim(production_lot) <> ''),
    production_time date NOT NULL,
    detection_stage text NOT NULL CHECK (detection_stage IN ('IQC', 'In-process', 'OQC', 'Customer', 'Other')),
    detection_time  date NOT NULL,
    affected_qty    integer NOT NULL CHECK (affected_qty > 0),
    disposition     text NOT NULL CHECK (btrim(disposition) <> ''),
    CONSTRAINT case_details_detection_after_production CHECK (detection_time >= production_time)
);

CREATE TABLE IF NOT EXISTS detail_failure_modes (
    detail_id       text NOT NULL REFERENCES case_details (detail_id) ON DELETE CASCADE,
    failure_mode_id text NOT NULL REFERENCES failure_modes (failure_mode_id),
    PRIMARY KEY (detail_id, failure_mode_id)
);

-- 5. Evidence：只挂 Case，不直接挂 Detail；Other 必须填写具体调查名称（CR-34）。
CREATE TABLE IF NOT EXISTS evidence_checkpoints (
    checkpoint_id   text PRIMARY KEY CHECK (btrim(checkpoint_id) <> ''),
    case_id         text NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    checkpoint_type text NOT NULL CHECK (checkpoint_type IN (
        'QC', 'AOI', 'Production', 'OCAP', 'Previous/Next Lot',
        'Monitoring', 'EDX', 'Reliability', 'Material', 'Other'
    )),
    custom_name     text CHECK (custom_name IS NULL OR btrim(custom_name) <> ''),
    result          text NOT NULL CHECK (btrim(result) <> ''),
    relevance       text NOT NULL CHECK (relevance IN ('related', 'not_related', 'uncertain')),
    CONSTRAINT evidence_custom_name_required_for_other
        CHECK (checkpoint_type <> 'Other' OR (custom_name IS NOT NULL AND btrim(custom_name) <> ''))
);

-- 6. CaseGroup / Membership：整表可为空，Case 不要求入组（结构 §4）。
CREATE TABLE IF NOT EXISTS case_groups (
    group_id               text PRIMARY KEY CHECK (btrim(group_id) <> ''),
    group_type             text[] NOT NULL,
    description            text NOT NULL CHECK (btrim(description) <> ''),
    other_type_description text CHECK (other_type_description IS NULL OR btrim(other_type_description) <> ''),
    CONSTRAINT case_groups_type_values CHECK (
        cardinality(group_type) > 0
        AND array_position(group_type, NULL) IS NULL
        AND group_type <@ ARRAY['repeat_case', 'same_abnormal_process', 'project',
                                'customer_request', 'management_request', 'other']::text[]
    ),
    CONSTRAINT case_groups_other_description_required CHECK (
        NOT ('other' = ANY (group_type))
        OR (other_type_description IS NOT NULL AND btrim(other_type_description) <> '')
    )
);

CREATE TABLE IF NOT EXISTS case_group_memberships (
    group_id           text NOT NULL REFERENCES case_groups (group_id) ON DELETE CASCADE,
    case_id            text NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    association_reason text NOT NULL CHECK (btrim(association_reason) <> ''),
    PRIMARY KEY (group_id, case_id)
);

-- 7. 辅助快照元数据：只保存快照身份、原文件哈希、非实体 payload 与源列表顺序。
-- 不进入实体表，也不给 Case 增加业务字段（任务包 §6）。
-- ordering 保存 Case/Detail/Evidence 等源列表与关系列表的顺序；payload 保存 queries、
-- corpus_version、generation_basis、query_revision、supersedes、case_families、sources
-- 等非实体内容；content_digest 是带版本的内容身份，imported_at 不参与该摘要。
CREATE TABLE IF NOT EXISTS snapshot_metadata (
    snapshot_id      text PRIMARY KEY CHECK (btrim(snapshot_id) <> ''),
    known_at         date NOT NULL,
    dataset_path     text NOT NULL CHECK (btrim(dataset_path) <> ''),
    dataset_sha256   text NOT NULL CHECK (btrim(dataset_sha256) <> ''),
    reference_path   text NOT NULL CHECK (btrim(reference_path) <> ''),
    reference_sha256 text NOT NULL CHECK (btrim(reference_sha256) <> ''),
    split            text NOT NULL CHECK (btrim(split) <> ''),
    review_status    text NOT NULL CHECK (btrim(review_status) <> ''),
    basis            text NOT NULL CHECK (btrim(basis) <> ''),
    payload          jsonb NOT NULL,
    ordering         jsonb NOT NULL,
    content_digest   text NOT NULL CHECK (btrim(content_digest) <> ''),
    digest_version   text NOT NULL CHECK (btrim(digest_version) <> ''),
    imported_at      timestamptz NOT NULL DEFAULT now()
);

-- 8. 索引：支持按 Case 取回内容及关联查询（结构 §5）。
CREATE INDEX IF NOT EXISTS case_details_case_id_idx ON case_details (case_id);
CREATE INDEX IF NOT EXISTS case_details_product_id_idx ON case_details (product_id);
CREATE INDEX IF NOT EXISTS evidence_checkpoints_case_id_idx ON evidence_checkpoints (case_id);
CREATE INDEX IF NOT EXISTS case_group_memberships_case_id_idx ON case_group_memberships (case_id);

-- 非密钥实例设置独立于业务资料，重复 prepare 不重置已保存值。
CREATE TABLE IF NOT EXISTS runtime_settings (
    singleton boolean PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    top_k integer NOT NULL CHECK (top_k BETWEEN 1 AND 10),
    max_tokens integer NOT NULL CHECK (max_tokens IN (2048, 4096))
);
INSERT INTO runtime_settings (singleton, top_k, max_tokens)
VALUES (TRUE, 4, 4096) ON CONFLICT (singleton) DO NOTHING;

