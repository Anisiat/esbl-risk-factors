--------------
-- Get demographics table

SELECT DISTINCT SUBJECT,
       DEATH_DATE,
       ETHNICITY,
       GENDER
FROM ICHT_PROD.ICARE_ICHT.ICARE_DEMOGRAPHIC_ANON
WHERE SUBJECT IN(
    SELECT DISTINCT SUBJECT
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
);


--------------
-- Get problems table

SELECT SUBJECT,
       ENCNTR_ID,
       PROBLEM_DESC,
       PROBLEM_DT_TM
FROM ICHT_PROD.ICARE_ICHT.ICARE_PROBLEMS_ANON
WHERE SUBJECT IN(
    SELECT DISTINCT SUBJECT
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
);


WITH all_administrations AS (
    SELECT
        SUBJECT,
        MEDICATION_NAME_SHORT,
        THERAPEUTICAL_CLASS,
        ADMINISTRATION_DT
    FROM ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON
)

SELECT DISTINCT
    s.SUBJECT,
    s.admission_date,
    a.MEDICATION_NAME_SHORT,
    a.THERAPEUTICAL_CLASS
FROM ICHT_SANDBOX_PROD.SMTPATH_25046.AT_ELECTIVE_SURGERY_MICRO_EKP s
INNER JOIN all_administrations a
    ON s.SUBJECT = a.SUBJECT
    AND lower(THERAPEUTICAL_CLASS) in ('cardiovascular agents',
     'central nervous system agents',
     'coagulation modifiers',
     'gastrointestinal agents',
     'genitourinary tract agents',
     'hormones/hormone modifiers',
     'metabolic agents',
     'miscellaneous agents',
     'nutritional products',
     'none',
     'respiratory agents',
     'topical agents')
   AND a.ADMINISTRATION_DT::DATE BETWEEN s.admission_date::DATE AND s.discharge_date::DATE;


--------------
-- Get abx administrations table

SELECT DISTINCT
    a.SUBJECT,
    a.encntr_id,
    a.administration_dt,
    a.MEDICATION_NAME_SHORT,
    a.THERAPEUTICAL_CLASS,
    a.ROUTE,
FROM ICHT_SANDBOX_PROD.SMTPATH_25046.AT_ELECTIVE_SURGERY_MICRO_EKP s
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_PHARMACY_ADMINISTRATION_ANON a
    ON s.SUBJECT = a.SUBJECT
    AND lower(THERAPEUTICAL_CLASS) = 'anti-infectives'
;

--==================================================================================================
-- get patient vitals

with duplicate_vitals AS(

SELECT v.SUBJECT,
       e.admission_date,
       v.OBSERVATION_PERFORMED_DT as temp_performed_dt,
       v.OBSERVATION_RESULT_CLEAN as temperature
FROM ICHT_SANDBOX_PROD.SMTPATH_25046.AT_ELECTIVE_SURGERY_MICRO_EKP e
INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_VITAL_SIGNS_ANON v
ON e.subject = v.subject
AND v.observation_performed_dt BETWEEN e.admission_date AND e.discharge_date
AND v.observation_name LIKE '%temperature%'
)
SELECT DISTINCT * FROM duplicate_vitals;

--==================================================================================================
-- get pathology data

with duplicate_pathology as(
SELECT  p.SUBJECT,
        p.ORDER_CODE,
        p.RESULT_AVAILABLE_DT,
        p.RESULT_CLEANED,
        p.RESULT_LOWER_RANGE,
        p.RESULT_UPPER_RANGE,
        p.SAMPLE_COLLECTED_DT,
        p.TEST_CODE,
        p.TEST_RESULT_UNIT
FROM ICHT_PROD.ICARE_ICHT.ICARE_PATHOLOGY_BLOOD_ANON p
INNER JOIN elective_surgery_micro_ekp_AT m
ON p.subject = m.subject
AND p.sample_collected_dt BETWEEN m.admission_date AND m.discharge_date
AND test_code in (SELECT DISTINCT TEST_CODE
FROM ICHT_SANDBOX_PROD.SMTPATH_25046.LOOKUP_PATHOLOGY_INFECTION_AT
))
SELECT DISTINCT * FROM duplicate_pathology
LIMIT 500
;


--==================================================================================================
-- get past healthcare exposure data

WITH cohort AS (
    SELECT
        subject,
        admission_date AS index_admission_date,
        discharge_date AS index_discharge_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_admissions AS (
    SELECT
        c.subject,
        c.index_admission_date,
        c.index_discharge_date,

        a.admission_date AS prior_admission_date,
        a.discharge_date AS prior_discharge_date,
        a.discharge_destination
    FROM cohort c
    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_ANON a
        ON c.subject = a.subject

    -- prior episode overlaps the 90d lookback window
    AND a.admission_date < c.index_admission_date
    AND COALESCE(a.discharge_date, a.admission_date) >= DATEADD(day, -90, c.index_admission_date)
)

SELECT DISTINCT subject, index_admission_date, prior_admission_date, prior_discharge_date
FROM prior_admissions;


--==================================================================================================
-- get past outpatient exposure data

WITH cohort AS (
    SELECT
        subject,
        admission_date AS index_admission_date,
        discharge_date AS index_discharge_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_OUTPATIENT_admissions AS (
    SELECT
        c.subject,
        c.index_admission_date,
        c.index_discharge_date,

        a.appointment_date,

    FROM cohort c
    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_OUTPATIENT_ANON a
        ON c.subject = a.subject

    -- prior episode overlaps the 90d lookback window
    AND a.appointment_date < c.index_admission_date
    AND a.appointment_date >= DATEADD(day, -90, c.index_admission_date)
)

SELECT DISTINCT subject, index_admission_date, appointment_date
FROM prior_OUTPATIENT_admissions;


--==================================================================================================
-- get past emergency exposure data

WITH cohort AS (
    SELECT
        subject,
        admission_date AS index_admission_date,
        discharge_date AS index_discharge_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_icu AS (
    SELECT
        c.subject,
        c.index_admission_date,
        i.arrival_date,
        i.departure_date
    FROM cohort c
    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EMERGENCY_ANON i
        ON c.subject = i.subject
        AND i.arrival_date < c.index_admission_date
        AND COALESCE(i.departure_date, i.arrival_date) >= DATEADD(day, -90, c.index_admission_date)
)

SELECT Distinct subject,
       index_admission_date,
       arrival_date,
       departure_date
FROM prior_icu;



--==================================================================================================
-- get crp pathology data

WITH duplicate_pathology AS (

    SELECT
        p.SUBJECT,
        m.admission_date,
        p.RESULT_AVAILABLE_DT,
        p.RESULT_CLEANED as crp,
        p.SAMPLE_COLLECTED_DT,

    FROM ICHT_PROD.ICARE_ICHT.ICARE_PATHOLOGY_BLOOD_ANON p

    INNER JOIN ICHT_SANDBOX_PROD.SMTPATH_25046.AT_ELECTIVE_SURGERY_MICRO_EKP m
        ON p.subject = m.subject
        AND p.sample_collected_dt BETWEEN m.admission_date AND m.discharge_date

    WHERE lower(p.test_code) LIKE '%crp%'
)

SELECT DISTINCT *
FROM duplicate_pathology;
