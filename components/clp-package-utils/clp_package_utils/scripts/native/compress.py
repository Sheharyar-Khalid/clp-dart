import argparse
import logging
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from bson import ObjectId
from pydantic import ValidationError
from pymongo import MongoClient

from clp_package_utils.general import get_clp_home
from clp_py_utils.clp_config import CLPConfig
from clp_py_utils.core import read_yaml_config_file
from compression_job_handler.common import JobStatus

# Setup logging
# Create logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
# Setup console logging
logging_console_handler = logging.StreamHandler()
logging_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
logging_console_handler.setFormatter(logging_formatter)
logger.addHandler(logging_console_handler)


def pretty_size(num, suffix="B"):
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return "%3.2f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.2f%s%s" % (num, "Yi", suffix)


def main(argv: List[str]) -> int:
    clp_home = get_clp_home()

    args_parser = argparse.ArgumentParser(description="Compress log files.")
    # fmt: off
    args_parser.add_argument(
        "--config", "-c", default=str(clp_home / "etc" / "clp-config.yaml"),
        help="CLP configuration file.",
    )
    args_parser.add_argument(
        "paths", metavar="PATH", nargs="*",
        help="Paths to compress.",
    )
    args_parser.add_argument(
        "-f", "--input-list", dest="input_list",
        help="A file listing all paths to compress.",
    )
    args_parser.add_argument(
        "--path-prefix-to-remove", metavar="DIR",
        help="Remove the given path prefix from each compressed file/dir.",
    )
    args_parser.add_argument(
        "--target-archive-size", type=int, help="Target archive size in bytes."
    )
    args_parser.add_argument(
        "--target-archive-dictionaries-data-size", type=int,
        help="Target data size of archive dictionaries in bytes.",
    )
    args_parser.add_argument(
        "--target-segment-size", type=int, help="Target segment size in bytes."
    )
    args_parser.add_argument(
        "--target-encoded-file-size", type=int, help="Target encoded file size in bytes."
    )
    args_parser.add_argument(
        "--target-num-archives", type=int, help="Target number of archives to create."
    )
    # fmt: off

    args = args_parser.parse_args(argv[1:])

    # Validate input paths are specified via one and only one method
    if args.input_list is None and 0 == len(args.paths):
        args_parser.error("No paths specified.")
    if len(args.paths) > 0 and args.input_list is not None:
        args_parser.error("Paths cannot be specified on the command line AND through a file.")

    # Validate target sizes
    target_size_options = [
        "--target-archive-size",
        "--target-archive-dictionaries-data-size",
        "--target-segment-size",
        "--target-encoded-file-size",
        "--target-num-archives",
    ]
    target_size_attributes = [opt[2:].replace("-", "_") for opt in target_size_options]
    for opt, attr in zip(target_size_options, target_size_attributes):
        value = getattr(args, attr)
        if value is not None and value <= 0:
            args_parser.error(f"{opt} must be greater than 0.")

    # Load configuration
    config_path = Path(args.config)
    try:
        clp_config = CLPConfig.parse_obj(read_yaml_config_file(config_path))
    except ValidationError as err:
        logger.error(err)
        return -1

    input_paths = []
    # Write the input path list file
    if args.input_list is None:
        for path_str in args.paths:
            stripped_path_str = path_str.strip()
            if "" == stripped_path_str:
                # Skip empty paths
                continue
            resolved_path = Path(stripped_path_str).resolve()
            input_paths.append(str(resolved_path))

    else:
        # Copy to jobs directory
        log_list_path = Path(args.input_list).resolve()
        with open(log_list_path, "r") as f:
            Lines = f.readlines()
            for line in Lines:
                input_paths.append(line)

    # Generate target size output config
    output_config = {}
    for attr in target_size_attributes:
        value = getattr(args, attr)
        if value is not None:
            output_config[attr] = value

    # Generate database entry to be inserted
    entry = {
        "input_type": "fs",
        "input_config": {"paths": input_paths},
        "status": "pending",
        "submission_timestamp": round(time.time() * 1000),
        "logs_uncompressed_size": 0,
        "logs_compressed_size": 0,
        "errors": False,
        "output_config": output_config,
    }
    if args.path_prefix_to_remove is not None:
        entry["input_config"]["path_prefix_to_remove"] = args.path_prefix_to_remove

    # Connect to database
    db_client: MongoClient = MongoClient(f"{clp_config.results_cache.get_uri()}")
    archive_db = db_client.get_database()

    jobs_collection = archive_db["cjobs"]

    job_id: Optional[ObjectId] = None
    try:
        # Submit job
        insert_result = jobs_collection.insert_one(entry)
        job_id = insert_result.inserted_id
        human_readable_job_id = f"job_{job_id}"

        logger.info(f"Compression {human_readable_job_id} submitted.")
        logger.info("Waiting for updates...")

        last_uncompressed_size = 0
        while True:
            time.sleep(1)

            # Get job status
            doc = jobs_collection.find_one(
                {"_id": job_id},
                projection={
                    "status": 1,
                    "begin_timestamp": 1,
                    "end_timestamp": 1,
                    "logs_uncompressed_size": 1,
                    "logs_compressed_size": 1,
                    "errors": 1,
                },
            )
            if doc is None:
                logger.error("Compression job disappeared.")
                return -1

            # Report progress
            uncompressed_size = doc["logs_uncompressed_size"]
            compressed_size = doc["logs_compressed_size"]
            if last_uncompressed_size < uncompressed_size:
                logger.info(
                    "Compressed {} into {} ({:.2f}%).".format(
                        pretty_size(uncompressed_size),
                        pretty_size(compressed_size),
                        float(compressed_size) / uncompressed_size * 100,
                    )
                )
                last_uncompressed_size = uncompressed_size

            # Handle job status
            try:
                job_status_str = doc["status"]
                job_status = JobStatus.from_str(job_status_str)
            except KeyError:
                logger.error(f"Unknown status `{job_status_str}` for `{human_readable_job_id}`.")
                return -1

            if JobStatus.FAILED == job_status:
                logger.error(f"Compression failed.")
                break
            if JobStatus.CANCELLED == job_status:
                logger.error("Compression cancelled.")
                break
            if JobStatus.DONE == job_status:
                runtime_delta = doc["end_timestamp"] - doc["begin_timestamp"]
                if isinstance(runtime_delta, timedelta):
                    runtime = runtime_delta.total_seconds()
                else:
                    # Should be in milliseconds timestamp
                    runtime = runtime_delta / 1000

                speed = last_uncompressed_size / runtime
                pretty_runtime = round(runtime, 2)
                pretty_speed = pretty_size(round(speed, 2))
                logger.info(
                    f"Compression finished. Runtime: {pretty_runtime}s. Speed: {pretty_speed}/s."
                )

                if doc["errors"]:
                    logger.error(
                        f"Errors occurred during compression."
                    )

                break
    except KeyboardInterrupt:
        logger.info("Trying to cancel compression job.")
        if job_id is not None:
            # Request cancellation if job hasn't started
            result = jobs_collection.find_one_and_update(
                filter={"_id": job_id, "status": str(JobStatus.PENDING)},
                update={"$set": {"status": str(JobStatus.CANCELLING)}},
            )
            if result is None:
                logger.error(
                    "Compression job already started and will continue to run in the background."
                )
            else:
                # Wait for job to cancel
                while True:
                    time.sleep(1)

                    doc = jobs_collection.find_one({"_id": job_id}, projection={"status": 1})
                    if doc is None:
                        logger.error("Compression job disappeared.")
                        return -1

                    job_status = JobStatus.from_str(doc["status"])
                    if JobStatus.CANCELLED == job_status or JobStatus.DONE == job_status:
                        logger.info("Cancelled compression jobs.")
                        break

    return 0


if "__main__" == __name__:
    sys.exit(main(sys.argv))