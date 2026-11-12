-- ============================================================
-- PRIOR INPATIENT EXPOSURE
-- Any inpatient spell overlapping the 90 days before index admission
-- ============================================================

WITH cohort AS (
    SELECT DISTINCT
        subject,
        spell_identifier,
        TRY_TO_DATE(admission_date) AS index_admission_date,
        TRY_TO_DATE(discharge_date) AS index_discharge_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_admissions AS (
    SELECT
        c.subject,
        c.spell_identifier,
        c.index_admission_date,

        TRY_TO_DATE(a.admission_date) AS prior_admission_date,
        TRY_TO_DATE(a.discharge_date) AS prior_discharge_date

    FROM cohort c

    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EPISODES_ANON a
        ON c.subject = a.subject

    WHERE TRY_TO_DATE(a.admission_date) < c.index_admission_date

      -- prior spell overlaps the 90-day lookback window
      AND COALESCE(
            TRY_TO_DATE(a.discharge_date),
            TRY_TO_DATE(a.admission_date)
          ) >= DATEADD(day, -90, c.index_admission_date)

      -- avoid matching the index admission itself if IDs/dates overlap
      AND NOT (
          TRY_TO_DATE(a.admission_date) = c.index_admission_date
          AND COALESCE(
                TRY_TO_DATE(a.discharge_date),
                TRY_TO_DATE(a.admission_date)
              ) = c.index_discharge_date
      )
)

SELECT DISTINCT
    subject,
    spell_identifier,
    index_admission_date,
    prior_admission_date,
    prior_discharge_date
FROM prior_admissions;

-- ============================================================
-- PRIOR EMERGENCY EXPOSURE
-- Emergency attendance within 90 days before index admission
-- ============================================================

WITH cohort AS (
    SELECT DISTINCT
        subject,
        spell_identifier,
        TRY_TO_DATE(admission_date) AS index_admission_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_emergency AS (
    SELECT
        c.subject,
        c.spell_identifier,
        c.index_admission_date,

        TRY_TO_DATE(e.arrival_date) AS arrival_date,
        TRY_TO_DATE(e.departure_date) AS departure_date

    FROM cohort c

    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_EMERGENCY_ANON e
        ON c.subject = e.subject

    WHERE TRY_TO_DATE(e.arrival_date) < c.index_admission_date
      AND TRY_TO_DATE(e.arrival_date)
            >= DATEADD(day, -90, c.index_admission_date)
)

SELECT DISTINCT
    subject,
    spell_identifier,
    index_admission_date,
    arrival_date,
    departure_date
FROM prior_emergency;


-- ============================================================
-- PRIOR OUTPATIENT EXPOSURE
-- Outpatient appointment within 90 days before index admission
-- ============================================================

WITH cohort AS (
    SELECT DISTINCT
        subject,
        spell_identifier,
        TRY_TO_DATE(admission_date) AS index_admission_date
    FROM AT_ELECTIVE_SURGERY_MICRO_EKP
),

prior_outpatient AS (
    SELECT
        c.subject,
        c.spell_identifier,
        c.index_admission_date,

        TRY_TO_DATE(o.appointment_date) AS appointment_date

    FROM cohort c

    INNER JOIN ICHT_PROD.ICARE_ICHT.ICARE_OUTPATIENT_ANON o
        ON c.subject = o.subject

    WHERE TRY_TO_DATE(o.appointment_date) < c.index_admission_date
      AND TRY_TO_DATE(o.appointment_date)
            >= DATEADD(day, -90, c.index_admission_date)
)

SELECT DISTINCT
    subject,
    spell_identifier,
    index_admission_date,
    appointment_date
FROM prior_outpatient;