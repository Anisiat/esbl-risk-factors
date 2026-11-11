-- =============================================================================
-- 03_microbiology.sql   (ports legacy get_microbiology_ekp.sql)
-- PURPOSE   Microbiology for the four eligible organisms, for cohort patients,
--           with antimicrobial susceptibility (AST) pivoted wide, plus FLAGS
--           saying whether the sample has enough information to judge ESBL
--           status. Nothing is dropped for lacking AST: flowchart steps 3-7
--           need those samples counted, and Python removes them at a logged step.
-- READS     ICARE_MICROBIOLOGY_ANON, AT_ELECTIVE_SURGERY_COHORT (subjects only),
--           sandbox lookups LOOKUP_CEPHALOSPORIN_R_AT, LOOKUP_CEPHALOSPORIN_S_AT,
--           AT_ABX_EXCLUDE
-- WRITES    AT_MICROBIOLOGY_EKP
--           grain: one row per (lab_test_id, organism_bug). All time, not only
--           the surgical spell (needed for prior-ESBL history).
-- FLAGS     has_ast_info        1 if the organism has a cephalosporin R/S result,
--                               an ESBL marker result (not "not determined") or a
--                               cefpodoxime result (legacy conditions 1-4)
--           esbl_statement      1 if a result row for this lab test AND organism
--                               reads "This is an Extended Spectrum Beta-Lactamase
--                               Producer (ESBL)" (string from the data dictionary)
--           esbl_statement_lab  same string anywhere in the lab test, any organism
--                               (kept because it is not known which organism the
--                               statement row is tagged with)
-- CHANGES vs legacy
--   * Restricted to cohort patients before pivoting (smaller, faster).
--   * Base rows come from ALL result rows of a lab test, so a sample with no AST
--     still appears (legacy dropped it).
--   * AST is no longer a filter but a flag; the ESBL statement is a new flag.
--   * Only 'antibiotic : result' rows are pivoted (free-text rows such as
--     "NO Carbapenem resistant organisms isolated" no longer become columns).
--   * The culture-type join is de-duplicated and also joins on organism_bug,
--     so mixed-organism samples keep one row per organism.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

CREATE OR REPLACE TABLE AT_MICROBIOLOGY_EKP AS
WITH cohort_subjects AS (
    SELECT DISTINCT SUBJECT FROM AT_ELECTIVE_SURGERY_COHORT
),

ekp_rows AS (
    SELECT m.*
    FROM ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON m
    INNER JOIN cohort_subjects c ON m.SUBJECT = c.SUBJECT
    WHERE LOWER(m.ORGANISM_BUG) IN (
        'escherichia coli',
        'klebsiella pneumoniae',
        'klebsiella oxytoca',
        'proteus mirabilis'
    )
),

-- One base row per lab test and organism. Prefer a culture row (order name is
-- not 'susceptibility') for site, dates and culture type.
base AS (
    SELECT
        SUBJECT,
        LAB_TEST_ID,
        ORGANISM_BUG,
        SITE,
        LATEST_COLLECT_DT,
        LATEST_RECEIVED_DT,
        LATEST_RESULT_DT,
        IFF(LOWER(ORDER_NAME) = 'susceptibility', NULL, ORDER_NAME) AS culture_type,
        IFF(LOWER(ORDER_NAME) = 'susceptibility', NULL, ORDER_CODE) AS culture_code,
        TRY_TO_TIMESTAMP(LATEST_COLLECT_DT::STRING)                 AS collect_ts
    FROM ekp_rows
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY LAB_TEST_ID, ORGANISM_BUG
        ORDER BY IFF(LOWER(ORDER_NAME) = 'susceptibility', 1, 0), ORDER_CODE, SITE
    ) = 1
),

r_lookup AS (SELECT DISTINCT LOWER(sensitivity) AS sensitivity FROM ICHT_SANDBOX_PROD.SMTPATH_25046.LOOKUP_CEPHALOSPORIN_R_AT),
s_lookup AS (SELECT DISTINCT LOWER(sensitivity) AS sensitivity FROM ICHT_SANDBOX_PROD.SMTPATH_25046.LOOKUP_CEPHALOSPORIN_S_AT),

-- get flags for each row: whether it has AST info and whether it has an ESBL statement
row_flags AS (
    SELECT
        r.LAB_TEST_ID,
        r.ORGANISM_BUG,
        IFF(
            lr.sensitivity IS NOT NULL
            OR ls.sensitivity IS NOT NULL
            OR (LOWER(r.SENSITIVITY) LIKE '%esbl markers%' AND LOWER(r.SENSITIVITY) NOT LIKE '%not determined%')
            OR LOWER(r.SENSITIVITY) LIKE '%cefpodoxime%',
            1, 0
        ) AS ast_row,
        IFF(LOWER(r.SENSITIVITY) LIKE '%extended spectrum beta-lactamase producer%', 1, 0) AS esbl_statement_row
    FROM ekp_rows r
    LEFT JOIN r_lookup lr ON LOWER(r.SENSITIVITY) = lr.sensitivity
    LEFT JOIN s_lookup ls ON LOWER(r.SENSITIVITY) = ls.sensitivity
),

-- get one row per lab test and organism, with flags for whether it has AST info and whether it has an ESBL statement
ast_flags AS (
    SELECT
        LAB_TEST_ID,
        ORGANISM_BUG,
        MAX(ast_row)            AS has_ast_info,
        MAX(esbl_statement_row) AS esbl_statement
    FROM row_flags
    GROUP BY LAB_TEST_ID, ORGANISM_BUG
),

-- Statement anywhere in the lab test, whatever organism the row is tagged with.
esbl_lab AS (
    SELECT DISTINCT m.LAB_TEST_ID
    FROM ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON m
    INNER JOIN cohort_subjects c ON m.SUBJECT = c.SUBJECT
    WHERE LOWER(m.SENSITIVITY) LIKE '%extended spectrum beta-lactamase producer%'
),

-- Split "Co-amoxiclav : R" into antibiotic_name = "Co-amoxiclav", result = "R".
split_ast AS (
    SELECT
        LAB_TEST_ID,
        ORGANISM_BUG,
        TRIM(SPLIT_PART(SENSITIVITY, ' : ', 1)) AS antibiotic_name,
        TRIM(SPLIT_PART(SENSITIVITY, ' : ', 2)) AS sensitivity_result
    FROM ekp_rows
    WHERE LOWER(ORDER_NAME) = 'susceptibility'
      AND SENSITIVITY LIKE '% : %'
),

split_ast_clean AS (
    SELECT *
    FROM split_ast
    WHERE antibiotic_name NOT IN (SELECT ANTIBIOTIC FROM AT_ABX_EXCLUDE WHERE ANTIBIOTIC IS NOT NULL)
),

-- Rows: lab test + organism. Columns: one per antibiotic. Values: S / I / R etc.
-- MAX() because there should be one result per antibiotic per test.
pivot_ast AS (
    SELECT *
    FROM split_ast_clean
    PIVOT (
        MAX(sensitivity_result)
        FOR antibiotic_name IN (SELECT DISTINCT antibiotic_name FROM split_ast_clean)
    )
)

-- Final table: base rows, plus pivoted AST columns, plus flags for whether the row has AST info and whether it has an ESBL statement.
SELECT
    b.*,
    COALESCE(f.has_ast_info, 0)                   AS has_ast_info,
    COALESCE(f.esbl_statement, 0)                 AS esbl_statement,
    IFF(l.LAB_TEST_ID IS NOT NULL, 1, 0)          AS esbl_statement_lab,
    p.* EXCLUDE (LAB_TEST_ID, ORGANISM_BUG)
FROM base b
LEFT JOIN pivot_ast p ON b.LAB_TEST_ID = p.LAB_TEST_ID AND b.ORGANISM_BUG = p.ORGANISM_BUG
LEFT JOIN ast_flags f ON b.LAB_TEST_ID = f.LAB_TEST_ID AND b.ORGANISM_BUG = f.ORGANISM_BUG
LEFT JOIN esbl_lab  l ON b.LAB_TEST_ID = l.LAB_TEST_ID
ORDER BY b.LAB_TEST_ID;


-- CHECK: one row per (lab test, organism); n_rows must equal n_distinct_keys.
SELECT
    COUNT(*)                                   AS n_rows,
    COUNT(DISTINCT LAB_TEST_ID, ORGANISM_BUG)  AS n_distinct_keys,
    COUNT_IF(has_ast_info = 1)                 AS n_has_ast_info,
    COUNT_IF(esbl_statement = 1)               AS n_esbl_statement,
    COUNT_IF(collect_ts IS NULL)               AS n_collect_ts_null,
    IFF(n_rows > 0 AND n_rows = n_distinct_keys, 'OK', 'CHECK') AS check_result
FROM AT_MICROBIOLOGY_EKP;
