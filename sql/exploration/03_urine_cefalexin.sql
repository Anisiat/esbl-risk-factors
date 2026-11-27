-- =============================================================================
-- 03_urine_cefalexin.sql
-- PURPOSE   Check the cascade-reporting explanation for missing ESBL status
--           (docs/discussion_notes.md section 1): for urine isolates in the
--           infection spells with NO third-generation cephalosporin or ESBL
--           marker result, which antibiotics WERE reported, and what cefalexin
--           showed.
-- HOW       Run after sql/pipeline 03 and 04. Three statements; screenshot the
--           results of statements 2 and 3. Nothing is exported to data/raw/.
-- APPROX.   Urine = site containing 'urin' (Python uses \burine\b|\burin\b).
--           Not restricted to Python's eligibility rules (postoperative, 48 h,
--           30 d), so counts are close to, not equal to, the 869 unknown samples.
-- =============================================================================

USE DATABASE ICHT_SANDBOX_PROD;
USE SCHEMA SMTPATH_25046;

-- 1. Urine isolates in infection spells without any 3GC or ESBL marker result.
CREATE OR REPLACE TEMPORARY TABLE AT_TMP_URINE_NO_3GC AS
SELECT DISTINCT m.LAB_TEST_ID, m.ORGANISM_BUG
FROM AT_ELECTIVE_SPELL_MICROBIOLOGY m
WHERE LOWER(m.SITE) LIKE '%urin%'
  AND m.esbl_statement_lab = 0
  AND NOT EXISTS (SELECT 1 FROM AT_MICROBIOLOGY_RESULTS r
                  WHERE r.LAB_TEST_ID = m.LAB_TEST_ID AND r.ORGANISM_BUG = m.ORGANISM_BUG
                    AND LOWER(TRIM(r.SENSITIVITY)) NOT LIKE 'cephalexin :%');   -- 3GC / marker rows only

-- 2. Which antibiotics were reported for them (one row per antibiotic).
SELECT SPLIT_PART(LOWER(TRIM(x.SENSITIVITY)), ' :', 1)  AS antibiotic,
       COUNT(DISTINCT x.LAB_TEST_ID, x.ORGANISM_BUG)    AS n_isolates,
       (SELECT COUNT(*) FROM AT_TMP_URINE_NO_3GC)        AS n_isolates_total
FROM AT_TMP_URINE_NO_3GC u
JOIN ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON x
  ON x.LAB_TEST_ID = u.LAB_TEST_ID AND x.ORGANISM_BUG = u.ORGANISM_BUG
GROUP BY 1
ORDER BY 2 DESC
LIMIT 25;

-- 3. Cefalexin results for them, by organism (one row per organism and result string).
SELECT LOWER(u.ORGANISM_BUG)                         AS organism,
       LOWER(TRIM(x.SENSITIVITY))                    AS cefalexin_result,
       COUNT(DISTINCT x.LAB_TEST_ID, x.ORGANISM_BUG) AS n_isolates
FROM AT_TMP_URINE_NO_3GC u
JOIN ICHT_PROD.ICARE_ICHT.ICARE_MICROBIOLOGY_ANON x
  ON x.LAB_TEST_ID = u.LAB_TEST_ID AND x.ORGANISM_BUG = u.ORGANISM_BUG
WHERE LOWER(x.SENSITIVITY) LIKE '%alexin%'
GROUP BY 1, 2
ORDER BY 1, 3 DESC;
