#!/usr/bin/env python3
# coding: utf-8


"""
Script to produce the evaluation for the HIPE Shared Task
"""

from ner_evaluation.ner_eval import Evaluator

import argparse
import logging
import csv
import pathlib
import json
import sys

import itertools

from datetime import datetime


FINE_COLUMNS = {"NE-FINE-LIT", "NE-FINE-METO", "NE-FINE-COMP", "NE-NESTED"}
COARSE_COLUMNS = {"NE-COARSE-LIT", "NE-COARSE-METO"}
NEL_COLUMNS = ["NEL-LIT", "NEL-METO"]


def parse_args():
    """Parse the arguments given with program call"""

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-r",
        "--ref",
        required=True,
        action="store",
        dest="f_ref",
        help="path to gold standard file in CONLL-U-style format",
    )

    parser.add_argument(
        "-p",
        "--pred",
        required=True,
        action="store",
        dest="f_pred",
        help="path to system prediction file in CONLL-U-style format",
    )

    parser.add_argument(
        "-l",
        "--log",
        action="store",
        default="clef_evaluation.log",
        dest="f_log",
        help="name of log file",
    )

    parser.add_argument(
        "-t",
        "--task",
        required=True,
        action="store",
        dest="task",
        help="type of evaluation",
        choices={"nerc_fine", "nerc_coarse", "nel"},
    )

    parser.add_argument(
        "--glueing_cols",
        required=False,
        action="store",
        dest="glueing_cols",
        help="provide two columns separated by a plus (+) whose label are glued together for the evaluation (e.g. COL1_LABEL.COL2_LABEL). \
        When glueing more than one pair, separate by comma",
    )

    parser.add_argument(
        "-n",
        "--n_best",
        required=False,
        action="store",
        dest="n_best",
        help="evaluate at particular cutoff value(s) for an ordered list of entity links, separate with a comma if multiple cutoffs. Link lists use a pipe as separator.",
    )

    parser.add_argument(
        "-u",
        "--union",
        required=False,
        action="store_true",
        dest="union",
        help="consider the union of the metonymic and literal annotation for the evaluation of NEL",
    )

    parser.add_argument(
        "-s",
        "--skip_check",
        required=False,
        action="store_true",
        dest="skip_check",
        help="skip check that ensures the prediction file is in line with submission requirements",
    )

    parser.add_argument(
        "-o",
        "--outdir",
        action="store",
        default=".",
        dest="outdir",
        help="name of output directory",
    )
    parser.add_argument(
        "--suffix",
        action="store",
        default="",
        dest="suffix",
        help="Suffix to append at output file names",
    )

    parser.add_argument(
        "--tagset", action="store", dest="f_tagset", help="file containing the valid tagset",
    )

    parser.add_argument(
        "--noise-level",
        action="store",
        dest="noise_level",
        help="evaluate NEL or NERC also on particular noise levels according to normalized Levenshtein distance of their manual OCR transcript. Example: 0.0-0.1,0.1-1.0",
    )

    parser.add_argument(
        "--time-period",
        action="store",
        dest="time_period",
        help="evaluate NEL or NERC also on particular time periods. Example: 1900-1950,1950-2000",
    )

    return parser.parse_args()


def enforce_filename(fname):

    try:
        f_obj = pathlib.Path(fname.lower())
        submission = f_obj.stem
        suffix = f_obj.suffix
        team, bundle, lang, n_submission = submission.split("_")
        bundle = int(bundle.lstrip("bundle"))

        assert suffix == ".tsv"
        assert lang in ("de", "fr", "en")
        assert bundle in range(1, 6)

    except (ValueError, AssertionError):
        msg = (
            f"The filename of the system response '{fname}' needs to comply with the shared task requirements. "
            + "Rename according to the following scheme: TEAMNAME_TASKBUNDLEID_LANG_RUNNUMBER.tsv"
        )
        logging.error(msg)
        raise AssertionError(msg)

    return submission, lang


def evaluation_wrapper(
    evaluator, cols, eval_type, n_best=1, noise_level=None, time_period=None, tags=None
):
    eval_global = {}
    eval_per_tag = {}

    for col in cols:
        eval_global[col], eval_per_tag[col] = evaluator.evaluate(
            col,
            eval_type=eval_type,
            merge_lines=True,
            n_best=n_best,
            noise_level=noise_level,
            time_period=time_period,
            tags=tags,
        )

        # add aggregated stats across types as artificial tag
        eval_per_tag[col]["ALL"] = eval_global[col]

    return eval_per_tag


def get_results(
    f_ref: str,
    f_pred: str,
    task: str,
    skip_check: bool = False,
    glueing_cols: str = None,
    n_best: list = [1],
    union: bool = False,
    outdir: str = ".",
    suffix: str = "",
    f_tagset: str = None,
    noise_levels: list = [None],
    time_periods: list = [None],
):

    if not skip_check:
        submission, lang = enforce_filename(f_pred)
    else:
        submission = f_pred
        lang = "LANG"

    if glueing_cols:
        glueing_pairs = glueing_cols.split(",")
        glueing_col_pairs = [pair.split("+") for pair in glueing_pairs]
    else:
        glueing_col_pairs = None

    if f_tagset:
        with open(f_tagset) as f_in:
            tagset = set(f_in.read().upper().splitlines())
    else:
        tagset = None

    evaluator = Evaluator(f_ref, f_pred, glueing_col_pairs)

    if task in ("nerc_fine", "nerc_coarse"):
        columns = FINE_COLUMNS if task == "nerc_fine" else COARSE_COLUMNS

        rows = []
        for noise_level, time_period in itertools.product(noise_levels, time_periods):
            eval_stats = evaluation_wrapper(
                evaluator,
                eval_type="nerc",
                cols=columns,
                tags=tagset,
                noise_level=noise_level,
                time_period=time_period,
            )
            eval_suffix = (
                f"{suffix + '-' if suffix else ''}"
                + define_time_label(time_period)
                + "-"
                + define_noise_label(noise_level)
            )

            fieldnames, rows_temp = assemble_tsv_output(submission, eval_stats, suffix=eval_suffix)
            rows += rows_temp

    elif task == "nel":

        rows = []
        # evaluate for various n-best
        for n, noise_level, time_period in itertools.product(n_best, noise_levels, time_periods):

            eval_suffix = (
                f"{suffix + '-' if suffix else ''}"
                + define_time_label(time_period)
                + "-"
                + define_noise_label(noise_level)
                + f"-@{n}"
            )

            if union:
                # nest columns to ensure iterating in parallel on both columns
                eval_stats = evaluation_wrapper(
                    evaluator,
                    eval_type="nel",
                    cols=[NEL_COLUMNS],
                    n_best=n,
                    noise_level=noise_level,
                    time_period=time_period,
                )
                eval_suffix = "union_lit_meto-" + eval_suffix

            else:
                eval_stats = evaluation_wrapper(
                    evaluator,
                    eval_type="nel",
                    cols=NEL_COLUMNS,
                    n_best=n,
                    noise_level=noise_level,
                    time_period=time_period,
                )

            fieldnames, rows_temp = assemble_tsv_output(
                submission, eval_stats, regimes=["fuzzy"], only_aggregated=True, suffix=eval_suffix,
            )
            rows += rows_temp

    if suffix:
        suffix = "_" + suffix

    f_sub = pathlib.Path(f_pred)
    f_tsv = str(pathlib.Path(outdir) / f_sub.name.replace(".tsv", f"_{task}{suffix}.tsv"))
    f_json = str(pathlib.Path(outdir) / f_sub.name.replace(".tsv", f"_{task}{suffix}.json"))

    with open(f_tsv, "w") as csvfile:
        writer = csv.DictWriter(csvfile, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(f_json, "w") as jsonfile:
        json.dump(
            eval_stats, jsonfile, indent=4,
        )


def define_noise_label(noise_level):
    if noise_level:
        noise_lower, noise_upper = noise_level
        return f"LED-{noise_lower}-{noise_upper}"
    else:
        return "LED-ALL"


def define_time_label(time_period):
    if time_period:
        date_start, date_end = time_period

        if all([True for date in [date_start, date_end] if date.day == 1 and date.month == 1]):
            # shorten label if only a year was provided (no particular month or day)
            date_start, date_end = date_start.strftime("%Y"), date_end.strftime("%Y")
        else:
            date_start, date_end = date_start.strftime("%Y"), date_end.strftime("%Y")

        return f"TIME-{date_start}-{date_end}"
    else:
        return "TIME-ALL"


def assemble_tsv_output(
    submission, eval_stats, regimes=["fuzzy", "strict"], only_aggregated=False, suffix=""
):

    metrics = ("P", "R", "F1")
    figures = ("TP", "FP", "FN")
    aggregations = ("micro", "macro_doc")

    fieldnames = [
        "System",
        "Evaluation",
        "Label",
        "P",
        "R",
        "F1",
        "F1_std",
        "P_std",
        "R_std",
        "TP",
        "FP",
        "FN",
    ]

    rows = []

    if suffix:
        suffix = "-" + suffix

    for col in sorted(eval_stats):
        for aggr in aggregations:
            for regime in regimes:

                eval_regime = f"{col}-{aggr}-{regime}{suffix}"
                # mapping terminology fuzzy->type
                regime = "ent_type" if regime == "fuzzy" else regime

                # collect metrics
                for tag in sorted(eval_stats[col]):

                    # collect only aggregated metrics
                    if only_aggregated and tag != "ALL":
                        continue

                    results = {}
                    results["System"] = submission
                    results["Evaluation"] = eval_regime
                    results["Label"] = tag
                    for metric in metrics:
                        mapped_metric = f"{metric}_{aggr}"
                        results[metric] = eval_stats[col][tag][regime][mapped_metric]

                    # add TP/FP/FN for micro analysis
                    if aggr == "micro":
                        for fig in figures:
                            results[fig] = eval_stats[col][tag][regime][fig]

                    if "macro" in aggr:
                        for metric in metrics:
                            mapped_metric = f"{metric}_{aggr}_std"
                            results[metric + "_std"] = eval_stats[col][tag][regime][mapped_metric]

                    for metric, fig in results.items():
                        try:
                            results[metric] = round(fig, 3)
                        except TypeError:
                            # some values are empty
                            pass

                    rows.append(results)

    return fieldnames, rows


def check_validity_of_arguments(args):
    if args.task != "nel" and (args.union or args.n_best):
        msg = "The provided arguments are not valid. Alternative annotations are only allowed for the NEL evaluation."
        logging.error(msg)
        raise AssertionError(msg)

    if args.union and args.n_best:
        msg = "The provided arguments are not valid. Restrict to a single evaluation schema for NEL, either a ranked n-best list or the union of the metonymic and literal column."
        logging.error(msg)
        raise AssertionError(msg)


def main():
    args = parse_args()

    # log to file
    logging.basicConfig(
        filename=args.f_log,
        filemode="w",
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # log errors also to console
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.ERROR)
    logging.getLogger().addHandler(handler)

    try:
        check_validity_of_arguments(args)
    except Exception as e:
        print(e)
        sys.exit(1)

    if not args.n_best:
        n_best = [1]
    else:
        n_best = [int(n) for n in args.n_best.split(",")]

    if args.noise_level:
        noise_levels = [level.split("-") for level in args.noise_level.split(",")]
        noise_levels = [tuple([float(lower), float(upper)]) for lower, upper in noise_levels]

        # add case to evaluate on all entities regardless of noise
        noise_levels = [None] + noise_levels

    else:
        noise_levels = [None]

    if args.time_period:
        time_periods = [period.split("-") for period in args.time_period.split(",")]
        try:
            time_periods = [
                (datetime.strptime(period[0], "%Y"), datetime.strptime(period[1], "%Y"))
                for period in time_periods
            ]
        except ValueError:
            time_periods = [
                (datetime.strptime(period[0], "%Y/%m/%d"), datetime.strptime(period[1], "%Y/%m/%d"))
                for period in time_periods
            ]
        # add case to evaluate on all entities regardless of period
        time_periods = [None] + time_periods
    else:
        time_periods = [None]

    try:
        get_results(
            args.f_ref,
            args.f_pred,
            args.task,
            args.skip_check,
            args.glueing_cols,
            n_best,
            args.union,
            args.outdir,
            args.suffix,
            args.f_tagset,
            noise_levels,
            time_periods,
        )
    except AssertionError as e:
        # don't interrupt the pipeline
        print(e)


################################################################################
if __name__ == "__main__":
    main()
    # "data/HIPE-data-v01-sample-de.tsv", "data/HIPE-data-v01-sample-de_pred.tsv"
