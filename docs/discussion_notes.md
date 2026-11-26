# Discussion notes for the paper

Points to raise in the Discussion or Limitations, with the evidence behind
them. Aggregate figures only; each cites the SDE run it came from.
References marked *(verify)* must be checked against the original before
citing.

## 1. Selective (cascade) reporting makes the outcome missing not at random

**Observation (SDE run on bundle 8ed8f07 + edits, 2026-11-26, eligible samples
before episodes):** a sample has no ESBL status when it has no third-generation
cephalosporin (3GC) result, no ESBL marker and no laboratory ESBL statement.

| Specimen site | Samples | No status | % |
|---|---|---|---|
| Urine | 1,275 | 869 | 68% |
| Every other site | 2 to 771 each | 0 to 15 each | 0 to 4% |

| Organism | Samples | No status | % |
|---|---|---|---|
| E. coli | 2,369 | 819 | 35% |
| P. mirabilis | 214 | 72 | 34% |
| K. pneumoniae | 700 | 24 | 3% |
| K. oxytoca | 131 | 3 | 2% |

So the missing status is almost entirely urinary E. coli and P. mirabilis.

**Likely mechanism (to confirm):** UK laboratories report urinary isolates
selectively (cascade reporting): a first-line panel (e.g. nitrofurantoin,
trimethoprim, cefalexin, amoxicillin, co-amoxiclav, mecillinam), with
second-line agents such as 3GCs reported only when the isolate is resistant to
first-line agents. Klebsiella is intrinsically resistant to ampicillin, so it
tends to receive the extended panel, which fits its low missingness. ESBLs
hydrolyse first-generation cephalosporins, so an ESBL producer would normally
be cefalexin-resistant and cascaded to 3GC testing. Untested urinary isolates
are therefore probably mostly non-ESBL.

**Consequence:** a complete-case analysis drops mainly non-ESBL urinary
episodes. This can inflate ESBL prevalence (34.5% in the manuscript, 39.6% in
this run) and the urine-vs-blood odds ratio (manuscript aOR 1.36; this run
6.50). This is selection bias from data missing not at random.

**Still to do before writing it up:**
- Confirm the cascade rule with the NW London Pathology microbiology team
  (which agents are first-line for urine, what triggers 3GC testing, and
  whether this changed over 2015-2025).
- Check in the data: among the unknown-status urine samples, the distribution
  of first-line results (especially cefalexin S/R). Needs a Snowflake query,
  because `microbiology_results.csv` keeps only 3GC and marker strings.
- Sensitivity analyses: (a) complete case (current); (b) untested urinary
  isolates that are cefalexin-susceptible counted as non-ESBL; (c) analysis
  excluding urine. Report how much the urine and prophylaxis estimates move.

**References:**
- UK Health Security Agency. UK Standards for Microbiology Investigations
  (SMI) B 41: Investigation of urine. *(verify current version)*
- UK Health Security Agency. UK SMI B 60: Detection of bacteria with
  extended-spectrum beta-lactamases. *(verify current version)*
- EUCAST. Guidelines for detection of resistance mechanisms and specific
  resistances of clinical and/or epidemiological importance, v2.0.
  *(verify current version)*
- McNulty CAM, Lasseter GM, Charlett A, et al. Does laboratory antibiotic
  susceptibility reporting influence primary care prescribing in urinary
  tract infection and other infections? J Antimicrob Chemother 2011;66:1396-404.
  *(verify)*
- Langford BJ, Daneman N, Diong C, et al. Antibiotic susceptibility reporting
  and association with antibiotic prescribing: a cohort study. Clin Microbiol
  Infect 2021;27:568-75. *(verify)*
- Paterson DL, Bonomo RA. Extended-spectrum beta-lactamases: a clinical
  update. Clin Microbiol Rev 2005;18:657-86. (ESBL substrate range, including
  first-generation cephalosporins.) *(verify)*
- Sterne JAC, White IR, Carlin JB, et al. Multiple imputation for missing data
  in epidemiological and clinical research: potential and pitfalls. BMJ
  2009;338:b2393. (Missing-data mechanisms; why complete case is biased when
  data are not missing at random.) *(verify)*
- Kaye KS, Harris AD, Samore M, Carmeli Y. The case-case-control study design:
  addressing the limitations of risk factor studies for antimicrobial
  resistance. Infect Control Hosp Epidemiol 2005;26:346-51. *(verify)*
