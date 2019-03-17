"""
This script updates the Navitia server with latest GTFS and OSM data.
Perquisites:
0. GTFS is zipped in ..\gtfs and osm file is in ..\osm
1. Navitia server is generated by Navitia Docker from here: https://github.com/shakedk/navitia-docker-compose
2. Navitia server is running with 2 coverages: default and secondary-cov
2a. Run "docker-compose -f docker-compose.yml -f docker-israel-custom-instances.yml up" in navitia-docker-compose folder
3. This script depends on:
    utils.py, gtfs2transfers.py and send_email.py that should all be in a sub-folder called
    configuration file called "monthly_update_config_params.conf" in the current working directory
scripts

What does this script do?

0. Get the current start of production dates of each coverage for later when we want to validate new data is present
1. Copy the existing secondary-cov.nav.lz4 to the host machine for backup and delete it from container
2. Generate the transfers table for Navitia and add it to the GTFS Zipped file (takes ~40 minutes)
3. Rename default.lz4 to secondary-cov.nav.lz4 (by that converting secondary-cov to last month gtfs)
4. Re-start Navitia docker with default cov. only to process GTFS & OSM (doesn't work when 2 coverages are running)
5. copy OSM & GTFS to the default coverage input folder on the worker container
6. Validate the conversion process is running by verifying the tyr_beat container is up (this container is the task
    dispatcher)
7. After 20 and 45 minutes - test that both osm and gtfs conversions are done to the graph format
8. Re-start Navitia to make sure all changes are applied with 2 coverages: default and secondary-cov
9. If it's up - delete the old gtfs and osm files
At the end: The default coverage shows the new GTFS & OSM and the previous default is now secondary-cov
10. Send success / failure e-mail to transitanalystisrael@gmail.com
"""

import utils
import datetime
import transitanalystisrael_config as cfg
import os
from pathlib import Path
from logger import _log


def process_new_data_to_current_coverage(docker_client, navitia_docker_compose_file_path,
                                         navitia_docker_compose_file_name, navitia_docker_compose_default_file_name,
                                         coverage_name, default_coverage_name,
                                         cov_eos_date, osm_file_path, osm_file_name,
                                         gtfs_file_path, gtfs_file_name, _log):

    start_processing_time = datetime.datetime.utcnow() #We take the time in UTC because docker time is in UTC
    # Re-start Navitia docker with default coverage only in order to process the OSM & GTFS
    # Later we will restart with the custom coverage as well
    utils.stop_all_containers(docker_client)
    if cfg.get_service_date == "auto":
        utils.start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                                 default_coverage_name)
    elif cfg.get_service_date == "on_demand":
        utils.start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                                 coverage_name)

    # Get the new worker container
    worker_con = docker_client.containers.list(filters={"name": "worker"})[0]

    # Copy OSM & GTFS to the default coverage input folder on the worker container
    if cfg.get_service_date == "auto":
        utils.copy_osm_and_gtfs_to_cov(worker_con, osm_file_path, osm_file_name, gtfs_file_path, gtfs_file_name,
                                       default_coverage_name)
    elif cfg.get_service_date == "on_demand":
        utils.copy_osm_and_gtfs_to_cov(worker_con, osm_file_path, osm_file_name, gtfs_file_path, gtfs_file_name,
                                       coverage_name)

    # Validate the conversion process takes place by ensuring tyr_beat is up
    if cfg.get_service_date == "auto":
        utils.validate_osm_gtfs_convertion_to_graph_is_running(docker_client, default_coverage_name,
                                                               navitia_docker_compose_file_path,
                                                               navitia_docker_compose_file_name)
    elif cfg.get_service_date == "on_demand":
        utils.validate_osm_gtfs_convertion_to_graph_is_running(docker_client, coverage_name,
                                                               navitia_docker_compose_file_path,
                                                               navitia_docker_compose_file_name)

    worker_con = docker_client.containers.list(filters={"name": "worker"})[0]
    # After 20 minutes - test that both osm and gtfs conversions are done
    success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 40, start_processing_time)

    # If it didn't succeed, give it 30 more minutes
    if not success:
        success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 30, start_processing_time)

    # If it didn't succeed, give it 30 more minutes
    if not success:
        success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 30, start_processing_time)

    if not success:
        _log.error("After 90 minutes - tasks aren't completed - connect to server for manual inspection")
        raise Exception

    is_changes_applied = True
    # Validate that changes are applied
    if cfg.get_service_date == "auto":
        is_changes_applied = utils.validate_auto_graph_changes_applied(coverage_name, default_coverage_name,
                                cov_eos_date, docker_client, navitia_docker_compose_file_path,
                                        navitia_docker_compose_file_name, navitia_docker_compose_default_file_name)
        if not is_changes_applied:
            raise Exception

    elif cfg.get_service_date == "on_demand":
        is_changes_applied = utils.validate_graph_changes_applied(coverage_name, coverage_name)
        if not is_changes_applied:
            raise Exception

    # If it's up - delete the old gtfs and osm files - only from AWS machines
    if is_changes_applied and utils.is_aws_machine():
        utils.delete_file_from_host(Path(os.getcwd()).parent / osm_file_path / osm_file_name)
        utils.delete_file_from_host(Path(os.getcwd()).parent / gtfs_file_path / gtfs_file_name)


# config variables to be moved to config-file downstrem
default_coverage_name, coverage_name, navitia_docker_compose_file_path, navitia_docker_compose_file_name, \
navitia_docker_compose_default_file_name, gtfs_file_path, gtfs_zip_file_name = utils.get_config_params()

try:

    # Get the docker service client
    docker_client = utils.get_docker_service_client()
    cov_sop_date = ""

    containers = docker_client.containers.list(filters={"name": "worker"})
    if len(containers) == 0:
        _log.error("Navitia docker is down, run 'docker-compose up' in the navitia-docker-compose repo folder")
        raise Exception
    # Get the worker container
    worker_con = containers[0]

    # For production env. we have default coverage and secondary-cov coverage so back up is needed
    if cfg.get_service_date == "auto":
        # Get the current start of production dates of default coverage for post-processing comparison
        if utils.is_cov_exists(worker_con, default_coverage_name):
            default_cov_sop_date = utils.get_coverage_start_production_date(default_coverage_name)
        if cov_sop_date is "":
            # There is no default covereage yet, assiging old date
            cov_sop_date = 19700101

        # Copy the existing secondary-cov.nav.lz4 to the host machine for backup and delete it from container
        if utils.is_cov_exists(worker_con, coverage_name):
            utils.backup_past_coverage(worker_con, coverage_name)
            utils.delete_grpah_from_container(worker_con, coverage_name)

        # Rename default.lz4 to secondary-cov.nav.lz4 (by that converting it to last month gtfs)
        if utils.is_cov_exists(worker_con, default_coverage_name):
            utils.move_current_to_past(worker_con, default_coverage_name, coverage_name)

    if cfg.get_service_date == "on_demand":
        utils.generate_ondemand_docker_config_file(navitia_docker_compose_file_path, navitia_docker_compose_file_name)

    # Generate the Transfers file required for Navitia and add to GTFS
    # utils.generate_gtfs_with_transfers(gtfs_zip_file_name, gtfs_file_path)

    process_new_data_to_current_coverage(docker_client, navitia_docker_compose_file_path,
                                         navitia_docker_compose_file_name, navitia_docker_compose_default_file_name,
                                         coverage_name, default_coverage_name,
                                         cov_sop_date, cfg.osmpath, cfg.osm_file_name,
                                         gtfs_file_path, gtfs_zip_file_name, _log)

except Exception as e:
    raise Exception
