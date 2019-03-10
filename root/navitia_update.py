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
                                         navitia_docker_compose_ondemand_file_path, osm_file_path,
                                         osm_file_name, gtfs_file_path, gtfs_file_name, secondary_custom_coverage_name,
                                         navitia_docker_compose_file_name, default_coverage_name, default_cov_eos_date,
                                         _log):

    # Re-start Navitia docker with default coverage only in order to process the OSM & GTFS
    # Later we will restart with the custom coverage as well
    utils.stop_all_containers(docker_client)
    if cfg.get_service_date == "auto":
        utils.start_navitia_with_default_coverage(navitia_docker_compose_file_path)
    elif cfg.get_service_date == "on_demand":
        utils.start_navitia_w_custom_cov(secondary_custom_coverage_name, navitia_docker_compose_file_path,
                                         navitia_docker_compose_ondemand_file_path, navitia_docker_compose_file_name)

    # Get the new worker container
    worker_con = docker_client.containers.list(filters={"name": "worker"})[0]


    # Copy OSM & GTFS to the default coverage input folder on the worker container
    if cfg.get_service_date == "auto":
        utils.copy_osm_and_gtfs_to_cov(worker_con, osm_file_path, osm_file_name, gtfs_file_path, gtfs_file_name, default_coverage_name)
    elif cfg.get_service_date == "on_demand":
        utils.copy_osm_and_gtfs_to_cov(worker_con, osm_file_path, osm_file_name, gtfs_file_path, gtfs_file_name, secondary_custom_coverage_name)

    # Validate the conversion process takes place by ensuring tyr_beat is up
    utils.validate_osm_gtfs_convertion_to_graph_is_running(docker_client, secondary_custom_coverage_name,
                                                           navitia_docker_compose_file_path,
                                                           navitia_docker_compose_ondemand_file_path,
                                                           navitia_docker_compose_file_name)

    # After 20 minutes - test that both osm and gtfs conversions are done
    success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 30)

    # If it didn't succeed, give it 30 more minutes
    if not success:
        success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 30)

    # If it didn't succeed, give it 30 more minutes
    if not success:
        success = utils.validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, 30)

    if not success:
        _log.error("After 90 minutes - tasks aren't completed - connect to server for manual inspection")
        raise Exception

    # Re-start Navitia to make sure all changesgtfs_zip_file_name are applied with default and custom coverages
    utils.stop_all_containers(docker_client)

    is_up = utils.start_navitia_w_custom_cov(secondary_custom_coverage_name, navitia_docker_compose_file_path,
                                             navitia_docker_compose_ondemand_file_path, navitia_docker_compose_file_name)

    # If it's up - delete the old gtfs and osm files
    if is_up:
        utils.delete_file_from_host(osm_file_name)
        utils.delete_file_from_host(gtfs_file_name)

    #Get the current worker container
    worker_con = docker_client.containers.list(filters={"name": "worker"})[0]

    # Validate new data is accessible via default and the old data is accessible via secondary
    utils.validate_graph_changes_applied(default_coverage_name, secondary_custom_coverage_name,
                                         default_cov_eos_date, worker_con)


# config variables to be moved to config-file downstrem
default_coverage_name, secondary_custom_coverage_name, navitia_docker_compose_file_path, \
    navitia_docker_compose_file_name, navitia_docker_compose_ondemand_file_path, \
        navitia_docker_compose_ondemand_file_name, gtfs_file_path, gtfs_zip_file_name = utils.get_config_params()

try:

    # Get the docker service client
    docker_client = utils.get_docker_service_client()

    containers = docker_client.containers.list(filters={"name": "worker"})
    if len(containers) == 0:
        _log.error("Navitia docker is down, run 'docker-compose up' in the navitia-docker-compose repo folder")
        raise Exception
    # Get the worker container
    worker_con = containers[0]

    default_cov_eos_date = ""

    # For production env. we have default coverage and secondary-cov coverage so back up is needed
    if cfg.get_service_date == "auto":
        # Get the current start of production dates of default coverage for post-processing comparison
        if utils.is_cov_exists(worker_con, default_coverage_name):
            default_cov_eos_date = utils.get_coverage_start_production_date(default_coverage_name)
        if default_cov_eos_date is "":
            # There is no default covereage yet, assiging old date
            default_cov_eos_date = 19700101

        # Copy the existing secondary-cov.nav.lz4 to the host machine for backup and delete it from container
        if utils.is_cov_exists(worker_con, secondary_custom_coverage_name):
            utils.backup_past_coverage(worker_con, secondary_custom_coverage_name)
            utils.delete_grpah_from_container(worker_con, secondary_custom_coverage_name)

        # Rename default.lz4 to secondary-cov.nav.lz4 (by that converting it to last month gtfs)
        if utils.is_cov_exists(worker_con, default_coverage_name):
            utils.move_current_to_past(worker_con, default_coverage_name, secondary_custom_coverage_name)

    if cfg.get_service_date == "on_demand":
        utils.generate_ondemand_docker_config_file(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                                   navitia_docker_compose_ondemand_file_path,
                                                   navitia_docker_compose_ondemand_file_name)

    # Generate the Transfers file required for Navitia and add to GTFS
    utils.generate_gtfs_with_transfers(gtfs_zip_file_name, gtfs_file_path)

    if cfg.get_service_date == "auto":
        process_new_data_to_current_coverage(docker_client, navitia_docker_compose_file_path,
                                             navitia_docker_compose_ondemand_file_path, cfg.osmpath, cfg.osm_file_name,
                                             gtfs_file_path, gtfs_zip_file_name, secondary_custom_coverage_name,
                                             navitia_docker_compose_file_name, default_coverage_name,
                                             default_cov_eos_date, _log)
    elif cfg.get_service_date == "on_demand":
            process_new_data_to_current_coverage(docker_client, navitia_docker_compose_file_path,
                                                 navitia_docker_compose_ondemand_file_path, cfg.osmpath,
                                                 cfg.osm_file_name, gtfs_file_path, gtfs_zip_file_name,
                                                 secondary_custom_coverage_name,
                                                 navitia_docker_compose_ondemand_file_name, default_coverage_name,
                                                 default_cov_eos_date, _log)

except Exception as e:
    raise Exception
