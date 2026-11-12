import numpy as np 
import pandas as pd
import logging
from pathlib import Path
import sys
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / 'data'
INTERIM_DIR = DATA_DIR / 'interim'

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils import data_cleaning_tools as dct
from src.utils import micro_cleaning_tools as mct
from src.steps.clean_microbiology import run as clean_microbiology

cfg = dct.load_config(PROJECT_ROOT /'configs'/'config.yaml')

def get_colonisation(
    main_df,
    cfg = cfg,
    join_cols=("subject", "admission_date"),
    new_col_name="colonisation",
):
    """
    Add a binary colonisation feature to the main dataframe.

    A patient/admission is flagged as colonised if there is at least one
    matching screening record for that subject and admission date.

    If no matching screening record is found, colonisation is assumed to be 0.

    Parameters
    ----------
    main_df : pd.DataFrame
        Main analysis dataframe.

    cfg_paths : dict
        Dictionary containing the path to the screens CSV under cfg_paths["screens"].

    join_cols : tuple or list of str, default ("subject", "admission_date")
        Columns used to match screening records to the main dataframe.

    new_col_name : str, default "colonisation"
        Name of the binary colonisation feature.

    Returns
    -------
    pd.DataFrame
        Copy of main_df with a binary colonisation column.
    """

    main_df = main_df.copy()

    if isinstance(join_cols, str):
        join_cols = [join_cols]
    else:
        join_cols = list(join_cols)

    cfg_paths = cfg['paths']

    screens_df = pd.read_csv(cfg_paths["screens"])
    screens_df = dct.clean_df_columns(screens_df)

    # screening_sites = ["rectal swab", "rectum", "rec", "recs"]

    ekp_organisms = [
        "escherichia coli",
        "klebsiella pneumoniae",
        "klebsiella oxytoca",
        "proteus mirabilis",
    ]

    # screens_df["site"] = screens_df["site"].str.lower().str.strip()
    screens_df["organism_bug"] = screens_df["organism_bug"].str.lower().str.strip()

    screens_df = screens_df[
        # screens_df["site"].isin(screening_sites) &
        screens_df["organism_bug"].isin(ekp_organisms)
    ].copy()

    exposed = (
        screens_df[join_cols]
        .drop_duplicates()
        .assign(**{new_col_name: 1})
    )

    main_df['admission_date'] = pd.to_datetime(main_df['admission_date'])
    

    main_df = main_df.merge(
        exposed,
        on=join_cols,
        how="left"
    )

    main_df[new_col_name] = main_df[new_col_name].fillna(0).astype(int)

    print(f"Added colonisation feature based on {len(exposed)} positive screening records.")
    
    return main_df



def load_clean_microbiology(cfg_path, micro_df_path = 'all_microbiology'):

    logging.getLogger().setLevel(logging.INFO)
        
    logging.info('Loading the config file...')

    cfg_path = Path(cfg_path)

    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path

    cfg = dct.load_config(cfg_path)
    paths = cfg.get('paths', {})
    config_cols = cfg.get('columns', {})

    # Load inputs
    logging.info("Loading input CSVs…")

    ceph_r_path = PROJECT_ROOT / paths['_3gcr']
    ceph_s_path = PROJECT_ROOT / paths['_3gcs']
    microbiology_path = PROJECT_ROOT / paths[micro_df_path]

    # starts with microbiology df which contains all surgery and micro info 

    microbiology_df = pd.read_csv(microbiology_path)
    ceph_r = pd.read_csv(ceph_r_path)
    ceph_s = pd.read_csv(ceph_s_path)

    
    # Clean, label, fix unknowns
    microbiology_df = dct.clean_df_columns(microbiology_df)

    # Building R/S rule sets…
    ceph_r_set, ceph_s_set = mct.get_ast_rules(ceph_r, ceph_s)

    logging.info("Labelling ESBL status…")
    microbiology_df['esbl_status'] = microbiology_df.apply(
        lambda row: mct.label_esbl_status(row, ceph_r_set, ceph_s_set),
        axis=1
    )

    logging.info("Dropping unknown ESBL status samples")
    microbiology_df = microbiology_df[microbiology_df['esbl_status'] != 'unknown']

    ekp_organisms = [
    "escherichia coli",
    "klebsiella pneumoniae",
    "klebsiella oxytoca",
    "proteus mirabilis",
    ]

    microbiology_df["organism_bug"] = microbiology_df["organism_bug"].str.lower().str.strip()

    microbiology_df = microbiology_df[microbiology_df["organism_bug"].isin(ekp_organisms)].copy()
    
    return microbiology_df




def get_prior_esbl_culture(
    df,
    cfg_path,
    index_col="subject", 
    micro_dt_col = 'latest_collect_dt', 
    admission_dt_col = 'admission_date',
    window_days = None
):
    """
    Add binary prior ESBL-positive culture flag.

    A subject/admission is flagged if any microbiology isolate with ESBL
    status 'ESBL' was collected before the admission date. If `window_days`
    is None (default, as in the manuscript run) there is no lookback limit
    ('ever'); otherwise only cultures within `window_days` days count.
    """

    df = df.copy()

    df[admission_dt_col] = pd.to_datetime(df[admission_dt_col], errors="coerce")

    microbiology_df = load_clean_microbiology(cfg_path = cfg_path)

    microbiology_df[micro_dt_col] = pd.to_datetime(microbiology_df[micro_dt_col], errors="coerce")

    merged_df = microbiology_df.merge(df[[index_col, admission_dt_col]], on = index_col, how = 'inner' )


    prior_esbl = (
        (merged_df[micro_dt_col] < merged_df[admission_dt_col])
        & (merged_df['esbl_status'] == 'ESBL')
    )

    if window_days is not None:
        window_start = merged_df[admission_dt_col] - pd.Timedelta(days = window_days)
        prior_esbl = prior_esbl & (merged_df[micro_dt_col] >= window_start)

    prior_df = (
        merged_df.assign(prior_positive_esbl = prior_esbl.astype(int))
        .groupby([index_col, admission_dt_col], as_index = False)['prior_positive_esbl']
        .max()
    )

    df = df.merge(
        prior_df, 
        on = [index_col, admission_dt_col], 
        how = 'left'
    )

    df['prior_positive_esbl'] = df['prior_positive_esbl'].fillna(0).astype(int)
    

    return df
