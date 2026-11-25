-- =============================================================================
-- 03_microbiology.sql   (ports legacy get_microbiology_ekp.sql)
-- PURPOSE   Microbiology for the four eligible organisms, for cohort patients,
--           (one row per sample), plus FLAGS saying whether the sample has
--           enough information to judge ESBL status, and a second table with the
--           raw susceptibility result strings (one row per result, NOT split or
--           pivoted). Nothing is dropped for lacking AST: Python labels ESBL status
--           from the result strings and removes episodes without a status at a
--           logged step.
-- READS     ICARE_MICROBIOLOGY_ANON, AT_ELECTIVE_SURGERY_COHORT (subjects only),
--           sandbox lookups LOOKUP_CEPHALOSPORIN_R_AT, LOOKUP_CEPHALOSPORIN_S_AT
--           (used for the has_ast_info flag only; Python reads lookups/*.csv)
-- WRITES    AT_MICROBIOLOGY_EKP
--           grain: one row per (lab_test_id, organism_bug). All time, not only
--           the surgical spell (needed for prior-ESBL history).
--           AT_MICROBIOLOGY_RESULTS
--             grain: one row per (subject, lab_test_id, organism_bug, sensitivity
--             string) for cefotaxime, ceftazidime, ceftriaxone, cefpodoxime,
--             cefixime and the ESBL marker. The string is kept exactly as in the
--             source (lower-cased), e.g. 'ceftriaxone : 4'.
-- FLAGS     has_ast_info        1 if the organism has a cephalosporin R/S result,
--                               an ESBL marker result (not "not determined") or a
--                               cefpodoxime result (legacy conditions 1-4)
--           esbl_statement      1 if a result row for this lab test AND organism
--                               reads "This is an Extended Spectrum Beta-Lactamase
--                               Producer (ESBL)" in SENSITIVITY. In practice this is
--                               always 0: checked in the SDE 2026-11-25, the lab stores
--                               the statement in ORGANISM_BUG, as its own "organism"
--                               row, not as a result of the E. coli/Klebsiella/Proteus row.
--           esbl_statement_lab  1 if the statement appears anywhere in the lab test
--                               (in ORGANISM_BUG; checked in the SDE 2026-11-25 that it
--                               never appears in SENSITIVITY). Which organism it
--                               belongs to is not recorded (counts: sql/exploration/02_esbl_statement_attribution.sql).
-- CHANGES vs legacy
--   * Restricted to cohort patients before pivoting (smaller, faster).
--   * Base rows come from ALL result rows of a lab test, so a sample with no AST
--     still appears (legacy dropped it).
--   * AST is no longer a filter but a flag; the ESBL statement is a new flag.
--   * No split or pivot: the legacy wide per-antibiotic columns are gone, the
--     result strings are exported as they are in AT_MICROBIOLOGY_RESULTS.
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
    WHERE LOWER(m.ORGANISM_BUG) LIKE '%extended spectrum beta-lactamase producer%'
)

-- Final table: base rows plus flags for whether the sample has AST info and whether it has an ESBL statement.
SELECT
    b.*,
    COALESCE(f.has_ast_info, 0)                   AS has_ast_info,
    COALESCE(f.esbl_statement, 0)                 AS esbl_statement,
    IFF(l.LAB_TEST_ID IS NOT NULL, 1, 0)          AS esbl_statement_lab
FROM base b
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


-- Raw susceptibility result strings used by Python's ESBL labelling (analysis_plan
-- section 6). Which strings count as resistant or susceptible is decided in
-- lookups/3GCR.csv and lookups/3GCS.csv, not here, so this filter only selects the
-- drugs and the ESBL marker by name.
CREATE OR REPLACE TABLE AT_MICROBIOLOGY_RESULTS AS
SELECT DISTINCT
    m.SUBJECT,
    m.LAB_TEST_ID,
    m.ORGANISM_BUG,
    LOWER(TRIM(m.SENSITIVITY)) AS sensitivity
FROM ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON m
INNER JOIN (SELECT DISTINCT SUBJECT FROM AT_ELECTIVE_SURGERY_COHORT) c ON m.SUBJECT = c.SUBJECT
WHERE LOWER(m.ORGANISM_BUG) IN ('escherichia coli', 'klebsiella pneumoniae', 'klebsiella oxytoca', 'proteus mirabilis')
  AND LOWER(TRIM(m.SENSITIVITY)) LIKE ANY (
        'cefotaxime :%', 'ceftazidime :%', 'ceftriaxone :%', 'cefpodoxime :%',
        'cefixime :%', 'esbl markers (ss = present) :%'
  );


-- CHECK: every result belongs to a sample in AT_MICROBIOLOGY_EKP, and rows are unique.
SELECT
    COUNT(*)                                                         AS n_result_rows,
    COUNT(DISTINCT r.SUBJECT, r.LAB_TEST_ID, r.ORGANISM_BUG, r.sensitivity) AS n_distinct_rows,
    COUNT_IF(e.LAB_TEST_ID IS NULL)                                  AS n_orphans,
    IFF(n_result_rows > 0 AND n_result_rows = n_distinct_rows AND n_orphans = 0, 'OK', 'CHECK') AS check_result
FROM AT_MICROBIOLOGY_RESULTS r
LEFT JOIN (SELECT DISTINCT LAB_TEST_ID, ORGANISM_BUG FROM AT_MICROBIOLOGY_EKP) e
    ON r.LAB_TEST_ID = e.LAB_TEST_ID AND r.ORGANISM_BUG = e.ORGANISM_BUG;
