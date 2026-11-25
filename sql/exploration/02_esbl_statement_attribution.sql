-- =============================================================================
-- 02_esbl_statement_attribution.sql
-- PURPOSE   One-off counts to decide how to attribute the lab's ESBL statement.
--           The lab stores "This is an Extended Spectrum Beta-Lactamase Producer
--           (ESBL)" as its own ORGANISM_BUG row (checked 2026-11-25), so it is not
--           tied to E. coli / Klebsiella / Proteus.
-- HOW       Run after sql/pipeline 03 and 04. One statement, one row of counts.
--           Nothing here is exported to data/raw/.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

WITH cohort AS (SELECT DISTINCT SUBJECT FROM AT_ELECTIVE_SURGERY_COHORT),
stmt_rows AS (
    SELECT m.LAB_TEST_ID, LOWER(TRIM(m.SENSITIVITY)) AS sensitivity
    FROM ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON m
    INNER JOIN cohort c ON m.SUBJECT = c.SUBJECT
    WHERE LOWER(m.ORGANISM_BUG) LIKE '%extended spectrum beta-lactamase producer%'
),
per_lab AS (
    SELECT LAB_TEST_ID, COUNT(*) AS n_eligible_organisms
    FROM AT_MICROBIOLOGY_EKP
    WHERE esbl_statement_lab = 1
    GROUP BY LAB_TEST_ID
)
SELECT
    (SELECT COUNT(DISTINCT LAB_TEST_ID) FROM stmt_rows)                     AS lab_tests_with_statement,
    (SELECT COUNT_IF(n_eligible_organisms = 1) FROM per_lab)                AS with_one_eligible_organism,
    (SELECT COUNT_IF(n_eligible_organisms > 1) FROM per_lab)                AS with_several_eligible_organisms,
    (SELECT COUNT(*) FROM AT_MICROBIOLOGY_EKP
      WHERE esbl_statement_lab = 1 AND has_ast_info = 0)                    AS eligible_rows_without_ast,
    (SELECT COUNT(*) FROM stmt_rows
      WHERE sensitivity LIKE ANY ('cefotaxime :%', 'ceftazidime :%', 'ceftriaxone :%',
                                  'cefpodoxime :%', 'cefixime :%'))         AS ceph_results_under_statement,
    (SELECT COUNT(*) FROM AT_ELECTIVE_SPELL_MICROBIOLOGY
      WHERE esbl_statement_lab = 1)                                         AS analysis_samples_with_statement;
-- lab_tests_with_statement minus the two "eligible organism" counts = statement lab
-- tests with no E. coli / Klebsiella / Proteus row at all.
