import docker
import progressbar
import json
import os
import requests
import subprocess
import time
import zipfile
import tarfile
import re
from io import BytesIO
import generate_transfers_table
from logger import _log
import transitanalystisrael_config as cfg
import process_date
from pathlib import Path
from datetime import datetime as dt
import glob
import process_date


def get_config_params():
    """
    Reads monthly_update_config_params.conf file and returns the configuration parameters
    :return: configuration parameters
    """
    # Get parameters
    default_coverage_name = cfg.default_coverage_name
    secondary_custom_coverage_name = cfg.secondary_custom_coverage_name
    # navitia_docker_compose_file_name = "docker-israel-custom-instances.yml"-needed only when 2 coverages should be up
    # Currently we only bring up one coverage and it's always the previous month to be aligned with past and current
    # websites. The regular coverage (most current) file is: "docker-compose.yml"
    coverage_name = secondary_custom_coverage_name
    navitia_docker_compose_file_name = "docker-compose-secondary-cov.yml"
    navitia_docker_compose_default_file_name =  "docker-compose.yml"
    navitia_docker_compose_file_path = Path(os.getcwd()).parent.parent / "navitia-docker-compose" / "compose_files"
    if cfg.get_service_date == "on_demand":
        navitia_docker_compose_file_name = "navitia-docker-ondemand-" + cfg.gtfsdate + ".yml"
        coverage_name = "ondemand-" + cfg.gtfsdate
    gtfs_file_path = Path(os.getcwd()).parent / cfg.gtfspath
    processdate = process_date.get_date_now()
    gtfs_zip_file_name = cfg.gtfsdirbase + processdate + ".zip"
    return default_coverage_name, coverage_name, navitia_docker_compose_file_path, navitia_docker_compose_file_name, \
           navitia_docker_compose_default_file_name, gtfs_file_path, gtfs_zip_file_name




def copy_file_into_docker(container, dest_path, file_path, file_name):
    """
    Copy a given file to a destination folder in a Docker container
    :param container: container object
    :param dest_path: destination folder path inside the container
    :param file_path: source path of the file on the host
    :param file_name: the file name to be copied
    """
    _log.info("Going to copy %s to %s at %s", file_name, container.name, dest_path)

    # Read the file
    file = open(Path(os.getcwd()).parent / file_path / file_name, 'rb')
    file = file.read()

    try:
        # Convert to tar file
        tar_stream = BytesIO()
        file_tar = tarfile.TarFile(fileobj=tar_stream, mode='w')
        tarinfo = tarfile.TarInfo(name=file_name)
        tarinfo.size = len(file)
        file_tar.addfile(tarinfo, BytesIO(file))
        file_tar.close()

        # Put in the container
        tar_stream.seek(0)
        success = container.put_archive(
            path=dest_path,
            data=tar_stream
        )
        if success:
            _log.info("Finished copying %s to %s at %s", file_name, container.name, dest_path)
        else:
            raise FileNotFoundError

    except FileNotFoundError as err:
        _log.error("Couldn't copy %s to %s at %s", file_name, container.name, dest_path)
        raise err


def get_docker_service_client():
    """
    Checks that the docker daemon service is running and returns the service client
    :return: the docker service client
    """
    # Check that the docker daemon service is up, and timeout after five minutes
    docker_check_alive_cmd = "docker info"
    docker_is_up = False
    timeout = time.time() + 60 * 5
    try:
        while not docker_is_up:
            if time.time() > timeout:
                raise TimeoutError
            # Check that the daemon is up and running
            docker_check_alive_process = subprocess.Popen(docker_check_alive_cmd, stdout=subprocess.PIPE, shell=True)
            output, error = docker_check_alive_process.communicate()
            docker_is_up = "Containers" in output.decode('utf-8')

        # Get the docker client
        client = docker.from_env()
        return client
    except BaseException as error:
        _log.error("Docker daemon service is not up")
        raise error


def get_navitia_url_for_cov_status(cov_name):
    """
    Get the url of Navitia coverage status page
    :param cov_name: the name of the coverage to return, e.g. "default" or "secondary-cov"
    :return: url of Navitia coverage status page
    """
    return "http://localhost:9191/v1/coverage/" + cov_name #+ "/status/"


def check_coverage_running(url, coverage_name):
    """
    Check if Navitia coverage is up and running
    :param url: Navitia server coverage url
    :param coverage_name: the name of the coverage to check
    :return: Whether a Navitia coverage is up and running
    """
    _log.info("checking if %s is up", coverage_name)
    response = requests.get(url)

    # Get the status of the coverage as Json
    json_data = json.loads(response.text)
    if "regions" not in json_data or "running" not in json_data["regions"][0]['status']:
        _log.info("%s coverage is down", coverage_name)
        return False
    else:
        _log.info("%s coverage is up", coverage_name)
    return True


def get_coverage_start_production_date(coverage_name):
    """
    Get the start production date of the current GTFS in the given coverage
    :param coverage_name: the name of the coverage
    :return: end of current production date
    """
    url = get_navitia_url_for_cov_status(coverage_name)
    response = requests.get(url)
    # Get the status of the coverage as Json
    json_data = json.loads(response.text)
    if "regions" not in json_data or "running" not in json_data["regions"][0]['status']:
        _log.debug("%s coverage is down so the start of production date can't be established", coverage_name)
        return ""
    else:
        start_production_date = json_data["regions"][0]["start_production_date"]
        return start_production_date

def check_prod_date_is_valid_using_heat_map(time_map_server_url, coverage_name, expected_valid_date):
    """
    Simulating UI action for heat map query with expected_valid_date
    :return: whether expected_valid_date is in production date
    """
    heat_map_url = time_map_server_url + coverage_name + \
                   "/heat_maps?max_duration=3600&from=34.79041%3B32.073443&datetime=" + expected_valid_date + \
                   "T080000+02:00&resolution=200"

    response = requests.get(heat_map_url)
    # Get the status of the coverage as Json
    json_data = json.loads(response.text)
    if "error" not in json_data:
        return True
    elif json_data['error']['message'] == "date is not in data production period":
        return False
    return True


def validate_auto_graph_changes_applied(coverage_name, default_coverage_name, default_cov_prev_sop_date, docker_client,
                                        navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                        navitia_docker_compose_default_file_name):
    """
    Validate that the new default coverage returns results for heat map query for current_start_service_date (as in dates file
    or gtfs date) and that secondary-cov has results for the previous production date of the default.

    :param default_coverage_name: The coverage that gets a new (usually more recent) start of production date
    :param secondary_custom_coverage_name: The coverage that gets a the original default_coverage start of production date
    :param default_cov_sop_date: start of production date of original default coverage (before changes applied)
    :return: whether the graph changes were applied
    """
    current_start_service_date = dt.strptime(process_date.get_date_now(), "%Y%m%d")

    if cfg.ttm_server_on == "aws_ec2":
        time_map_server_url = cfg.time_map_server_aws_url
    else:
        time_map_server_url = cfg.time_map_server_local_url

    # Check that the current default coverage is up-to-date by comparing sop dates
    stop_all_containers(docker_client)

    start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_default_file_name,
                                       default_coverage_name, False)

    if not check_prod_date_is_valid_using_heat_map(time_map_server_url, default_coverage_name,
                                                   current_start_service_date.strftime("%Y%m%d")):
        _log.error("The %s coverage seems not to be up-to-date following update attempts.", default_coverage_name)
        return False
    else:
        _log.info("%s coverage is up-to-date with production date %s", default_coverage_name,
                  current_start_service_date.strftime("%Y%m%d"))

    # Check that the coverage_name (the previous one) is up-to-date by comparing sop dates
    stop_all_containers(docker_client)

    is_up = start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                       coverage_name, False)
    if not is_up:
        _log.error("The %s coverage seems not to be up", coverage_name)
    cov_sop_date = get_coverage_start_production_date(coverage_name)
    if cov_sop_date == "":
        _log.info("If this is the first time you're running Transit Analyst Israel data processing, you need to "
                  "copy the generated default.nav.lz4 graph to secondary-cov.nav.lz4 - See docs.")
        return True

    if not check_prod_date_is_valid_using_heat_map(time_map_server_url, coverage_name,
                                                   current_start_service_date.strftime("%Y%m%d")):
        _log.error("The %s coverage seems not to be up-to-date following update attempts.\nA call for heat map data with"
                   " %s date returned no results", coverage_name, current_start_service_date.strftime("%Y%m%d"))
        return False
    _log.info("%s coverage is now updated with new start-of-production date %s. "
              "Can be accessed via %s%s", coverage_name, current_start_service_date.strftime("%Y%m%d"), time_map_server_url,
              coverage_name)
    return True

def validate_graph_changes_applied(coverage_name):
    """
    Validate that the coverage has a different start of production date different from before
    """
    current_start_service_date = process_date.get_date_now()

    if cfg.ttm_server_on == "aws_ec2":
        time_map_server_url = cfg.time_map_server_aws_url
    else:
        time_map_server_url = cfg.time_map_server_local_url

    cov_sop_date = get_coverage_start_production_date(coverage_name)
    if cov_sop_date == "" or not check_prod_date_is_valid_using_heat_map(time_map_server_url, coverage_name,
                                                                     current_start_service_date):
        _log.error("The %s coverage seems not to be up-to-date following update attempts."
                   "\n A call for heat map data with %s date returned no results",
                   coverage_name, current_start_service_date)
        return False

    _log.info("%s coverage is now updated with new start-of-production date %s\n."
              "Can be accessed via %s%s", coverage_name, current_start_service_date, time_map_server_url,
              coverage_name)
    return True

def start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                       coverage_name, extend_wait_time=False):
    """
    Start Navitia server with only default coverage (using docker-compose)
    :param navitia_docker_compose_file_path: path where docker-compose file exists
    :param extend_wait_time: whether an extended time of wait should be applied. Should be set to True when Navitia
    docker compose is started up the first time (images are being downloaded from the web)
    :return: Whether Navitia was started successfully with default coverage
    """

    _log.info("Attempting to start Navitia with %s coverage", coverage_name)

    # run the docker- compose and redirect logs to prevent from printing in the output
    navitia_docker_start_command = "docker-compose -f" + navitia_docker_compose_file_name + " -p navitia-docker-compose up --remove-orphans"

    subprocess.Popen(navitia_docker_start_command, shell=True, cwd=navitia_docker_compose_file_path, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    # Longer wait time is required because images are being re-downloaded
    if extend_wait_time:
        t_wait = 60 * 5
    else:
        t_wait = 60 * 3
    _log.info("Waiting %s seconds to validate Navitia docker is up and running", t_wait)
    time.sleep(t_wait)

    # Check if coverage is up and running
    is_default_up = check_coverage_running(get_navitia_url_for_cov_status(coverage_name), coverage_name)
    if not is_default_up:
        return False
    return True


def start_navitia_w_default_and_custom_cov(secondary_custom_coverage_name, navitia_docker_compose_file_path,
                                           navitia_docker_compose_custom_file_path, navitia_docker_compose_file_name,
                                           extend_wait_time=False):
    """
    Start Navitia server with default and custom coverages (using custom docker-compose file)

    :param secondary_custom_coverage_name:
    :param navitia_docker_compose_file_path: path where docker-compose file exists
    :param navitia_docker_compose_file_name:  name of the custom docker-compose file
        :param extend_wait_time: whether an extended time of wait should be applied. Should be set to True when Navitia
    docker compose is started up the first time (images are being downloaded from the web)
    :return:  Whether Navitia was started successfully with default and secondary coverages
    """

    _log.error("This method isn't currently used because 2 corages require server with at least 10GB RAM available for "
               "docker.\nEach coverage requires about 3.5 RAM when running")
    raise Exception
    # Verifying the custom file has another coverage named secondary_custom_coverage_name which isn't "default"
    # _log.info("Attempting to start Navitia with default coverage and %s coverage", secondary_custom_coverage_name)
    # navitia_docker_compose_custom_file = open(os.path.join(navitia_docker_compose_custom_file_path,
    #                                                        navitia_docker_compose_file_name), mode='r')
    # navitia_docker_compose_custom_file_contents = navitia_docker_compose_custom_file.read()
    # navitia_docker_compose_custom_file.close()
    #
    # if secondary_custom_coverage_name != "default" \
    #         and not secondary_custom_coverage_name in navitia_docker_compose_custom_file_contents:
    #     _log.error("The custom configuration does not include a coverage area named: %s. Fix config, restart docker "
    #                "and start again", secondary_custom_coverage_name)
    #     return False
    #
    #     # run the docker- compose and redirect logs to prevent from printing in the output
    # regular_docker_compose_file_full_path = str(Path(navitia_docker_compose_file_path) / "docker-compose.yml")
    # navitia_docker_start_command = "docker-compose -f " + regular_docker_compose_file_full_path + " -f " + \
    #                                navitia_docker_compose_file_name + " -p navitia-docker-compose up --remove-orphans"
    #
    # subprocess.Popen(navitia_docker_start_command, shell=True, cwd=navitia_docker_compose_custom_file_path, stderr=subprocess.DEVNULL,
    #                  stdout=subprocess.DEVNULL)
    # # Longer wait time is required because images are being re-downloaded
    # if extend_wait_time:
    #     t_wait = 60 * 5
    # else:
    #     t_wait = 240
    # _log.info("Waiting %s seconds to validate Navitia docker is up and running", t_wait)
    # time.sleep(t_wait)
    #
    # # Check if default and secondary_custom_coverage_name regions are up and running
    # if cfg.get_service_date == 'auto':
    #     default_coverage_name = "default"
    #     is_default_up = check_coverage_running(get_navitia_url_for_cov_status(default_coverage_name), default_coverage_name)
    #     if not is_default_up:
    #         return False
    # is_secondary_up = check_coverage_running(get_navitia_url_for_cov_status(secondary_custom_coverage_name),
    #                                          secondary_custom_coverage_name)
    # if not is_secondary_up:
    #     return False
    # return True

def generate_ondemand_docker_config_file(navitia_docker_compose_file_path, navitia_docker_compose_file_name):
    '''
    Creates a custom docker-compose file for on-demand environment from the docker-israel-custom-instances.yml file
    '''
    navitia_docker_compose_file = open(os.path.join(navitia_docker_compose_file_path, 'docker-compose.yml'),
                                       mode='r')
    navitia_docker_compose_file_contents = navitia_docker_compose_file.read()
    navitia_docker_compose_file.close()

    custom_custom_docker_file_contents = navitia_docker_compose_file_contents.replace("default",
                                                                                        "ondemand-" + cfg.gtfsdate)

    with open(os.path.join(navitia_docker_compose_file_path, navitia_docker_compose_file_name),
              mode='w+') as custom_custom_docker_file:
        custom_custom_docker_file.write(custom_custom_docker_file_contents)
    custom_custom_docker_file.close()
    _log.info("Created custom docker-compose file: %s", navitia_docker_compose_file_name)


def move_current_to_past(container, source_cov_name, dest_cov_name):
    """
    Move the Navitia graph of the source coverage to the destination coverage so in next re-start changes are applied
    :param container: the worker container of Navitia
    :param source_cov_name: the name of the coverage to take the graph from (usually "default")
    :param dest_cov_name: the name of the coverage to move the graph to (e.g. "secondary-cov")
    :return: whether the move was successful, a RunTimeError is thown if not
    """
    command_list = "/bin/sh -c \"mv " + source_cov_name + ".nav.lz4 "+ dest_cov_name + ".nav.lz4\""
    exit_code, output = container.exec_run(cmd=command_list,  stdout=True, workdir="/srv/ed/output/")
    if exit_code != 0:
        _log.error("Couldn't change %s to %s", source_cov_name, dest_cov_name)
        raise RuntimeError
    _log.info("Changed the name of %s.nav.lz4 to %s.nav.lz4", source_cov_name, dest_cov_name)
    return True

def is_cov_exists(container, coverage_name):
    _log.info("Checking if %s exists in /srv/ed/output of %s", coverage_name, container.name)
    file_list_command = "/bin/sh -c \"ls\""
    exit_code, output = container.exec_run(cmd=file_list_command, stdout=True, workdir="/srv/ed/output/")
    exists = coverage_name in str(output)
    if exists:
        _log.info("%s exists in /srv/ed/output of %s", coverage_name, container.name)
    else:
        _log.info("%s doesn't exists in /srv/ed/output of %s", coverage_name, container.name)
    return exists

def backup_past_coverage(container, coverage_name):
    """
    Copy a given coverage graph to the local host running this script
    :param container: Navitia worker container
    :param coverage_name: the coverage graph name to copy
    """
    # Create a local file for writing the incoming graph
    local_processed_folder = Path(os.getcwd()).parent / "processed"
    _log.info("Going to copy %s.nav.lz4 to %s on local host", coverage_name, local_processed_folder)
    local_graph_file = open(os.path.join(local_processed_folder, coverage_name + '.nav.lz4'), 'wb')

    # Fetch the graph file
    bits, stat = container.get_archive('/srv/ed/output/' + coverage_name + '.nav.lz4')
    size = stat["size"]

    # Generate a progress bar
    pbar = createProgressBar(size, action="Transferring")

    # Fetch
    size_iterator = 0
    for chunk in bits:
        if chunk:
            file_write_update_progress_bar(chunk, local_graph_file, pbar, size_iterator)
            size_iterator += len(chunk)
    local_graph_file.close()
    pbar.finish()
    _log.info("Finished copying %s.nav.lz4 to %s on local host", coverage_name, os.getcwd())


def delete_grpah_from_container(container, coverage_name):
    """
    Delete a graph from Navitia worker container
    :param container: Navitia worker container
    :param coverage_name: the name of the coverage that its graph should be removed
    """
    return delete_file_from_container(container, coverage_name + ".nav.lz4")


def delete_file_from_container(container, file_name):
    """
    Delete a filefrom Navitia worker container
    :param container: Navitia worker container
    :param file_name: the name of the file to be removed
    """
    delete_command= "/bin/sh -c \"rm " + file_name + "\""
    exit_code, output = container.exec_run(cmd=delete_command,  stdout=True, workdir="/srv/ed/output/")
    if exit_code != 0:
        _log.error("Couldn't delete %s graph", file_name)
        return False
    _log.info("Finished deleting %s from container %s", file_name, container.name)


def delete_file_from_host(file_name):
    """
    Delete a file from the host running this script
    :param file_name: the file name to be deleted
    """
    if os.path.isfile(file_name):
        os.remove(file_name)
        _log.info("Finished deleting %s from host", file_name)


def stop_all_containers(docker_client):
    """
    Stop all the running docker containers
    :param docker_client: docker client
    """
    _log.info("Going to stop all Docker containers")
    for container in docker_client.containers.list():
        container.stop()
    _log.info("Stopped all Docker containers")


def generate_transfers_file(gtfs_file_path):
    """
    Generate a transfers table compatible with Navitia's server requirements for extending transfers between stops in
    graph calculation. Default values are used:
    maximum crow-fly walking distance of 500 meters, 0 minimum transfer time and  0.875 meters/second walking speed
    :param gtfs_file_name: Name of GTFS zip file containing a stops.txt file with list of stops and their coordinates
    :return: the full path of the generated transfers.txt file
    """
    output_path = os.path.join(gtfs_file_path,"transfers.txt")
    generate_transfers_table.generate_transfers(input=os.path.join(gtfs_file_path,"stops.txt"), output=output_path)
    return output_path


def generate_gtfs_with_transfers(gtfs_file_name, gtfs_file_path):
    """
    Generate a GTFS ZIP file with a processed transfers.txt file compatible with Navitia's server requirements for
    extending transfers between stops in graph calculation
    :param gtfs_file_name: GTFS zip file name
    :param gtfs_file_path: GTFS zip file path
    :return: the name of the GTFS file
    """
    gtfs_file_path_name = os.path.join(gtfs_file_path, gtfs_file_name)

    _log.info("Extracting stops.txt and computing transfers.txt")
    output_path = generate_transfers_file(os.path.join(gtfs_file_path,gtfs_file_name[:-4]))
    with zipfile.ZipFile(gtfs_file_path_name, 'a') as zip_ref:
        zip_ref.write(output_path, arcname="transfers.txt")
    _log.info("Added transfers.txt to %s", gtfs_file_path_name)


def  copy_osm_and_gtfs_to_cov(worker_con, osm_file_path, osm_file_name, gtfs_file_path, gtfs_file_name, cov_name):
    """
    Copy GTFS and OSM files into the input folder of default coverage for creating a new Navitia graph
    :param worker_con: docker worker container
    :param osm_file_path: osm file path
    :param osm_file_name: osm file name
    :param gtfs_file_path: gtfs file path
    :param gtfs_file_name: gtfs file name
    :param cov_name: coverage name
    :return:
    """
    copy_file_into_docker(worker_con, 'srv/ed/input/' + cov_name, osm_file_path, osm_file_name)
    copy_file_into_docker(worker_con, 'srv/ed/input/' + cov_name, gtfs_file_path, gtfs_file_name)

def validate_osm_gtfs_convertion_to_graph_is_completed(worker_con, time_to_wait, start_processing_time):
    """
    Validates that the following Navitia worker tasks were successfully completed:
    osm2ed, gtfs2ed and ed2nav
    :param worker_con: the Navitia worker container
    :param time_to_wait: time to wait for the validation to take place, in minutes. Default is 20 minutes
    :return: Whether conversion is completed or not
    """

    # Wait if needed
    _log.info("Waiting %s minutes to let OSM & GTFS conversions to lz4 graph takes place", time_to_wait)
    time.sleep(time_to_wait * 60)
    _log.info("I'm back! Verifying that the conversions took place")
    # Success status look like Task tyr.binarisation.ed2nav[feac06ca-51f7-4e39-bf1d-9541eaac0988] succeeded
    # and tyr.binarisation.gtfs2ed[feac06ca-51f7-4e39-bf1d-9541eaac0988] succeeded
    tyr_worker_outputname = "tyr_worker_output.txt"

    with open(tyr_worker_outputname, "w", encoding="UTF-8") as tyr_worker_output:
        tyr_worker_output.write(worker_con.logs().decode('utf-8'))
    tyr_worker_output.close()

    ed2nav_completed = False
    with open(tyr_worker_outputname, "r", encoding="UTF-8") as tyr_worker_output:
        lines = tyr_worker_output.readlines()
        for line in reversed(lines):
            if re.compile(r'tyr\.binarisation\.ed2nav\[\S*\] succeeded').search(line):
                time_of_line = re.findall(r'\d{1,4}-\d{1,2}-\d{1,2}\b \d{1,2}:\d{1,2}:\d{1,2}', line)
                time_of_line = dt.strptime(time_of_line[0], '%Y-%m-%d %H:%M:%S')
                if start_processing_time < time_of_line:
                    ed2nav_completed = True
                    break
    os.remove(tyr_worker_outputname)
    if ed2nav_completed:
        _log.info("OSM conversion task ed2nav, GTFS conversion task gtfs2ed  and ed2nav are successful")
        return True
    else:
        _log.error("After %s minutes - tasks aren't completed", time_to_wait)
        return False

def validate_osm_gtfs_convertion_to_graph_is_running(docker_client, coverage_name, navitia_docker_compose_file_path,
                                                     navitia_docker_compose_file_name):
    """
    Validates that the conversion of gtfs & OSM to Navitia graph is undergoing (continious process).
    Container tyr_beat is the service that triggers the conversion in the worker container and it does this after
    new files are copied into /srv/ed/input/<coverage-name> folder in the worker container.
    If tyr_beat is down and can't be re-started, the container an its image are removed and re-downloaded from the web
    :param docker_client: the docker client
    :param secondary_custom_coverage_name: the secondary custom coverage
    :param navitia_docker_compose_file_path:
    :param navitia_docker_compose_file_name:
    :return:
    """
    # tyr_beat must be running as it manages the tasks for the worker, the latter generates the graph
    _log.info("Validating that tyr_beat is up and running")
    beat_con = docker_client.containers.list(filters={"name": "beat"})

    time_beat_restarted=""
    if not beat_con:
        # restarting tyr_beat
        _log.info("tyr_beat is down, attempting to re-run")
        tyr_beat_start_command = "docker-compose -f" + navitia_docker_compose_file_name + " -p navitia-docker-compose up tyr_beat"
        time_beat_restarted = dt.utcnow()
        with open("tyr_beat_output.txt", "w", encoding="UTF-8") as tyr_beat_output:
            subprocess.Popen(tyr_beat_start_command, cwd=navitia_docker_compose_file_path,
                                                shell=True, stdout=tyr_beat_output, stderr=tyr_beat_output)
        # Wait 30 seconds for it to come up
        _log.info("Waiting 15 seconds to see if tyr_beat is up")
        time.sleep(15)
        tyr_beat_output.close()

        # Check that tyr_beat is working using it's log and comparing the restart time with time in the log
        new_time_is_found = False
        with open("tyr_beat_output.txt", "r", encoding="UTF-8") as tyr_beat_output:
            lines = tyr_beat_output.readlines()
            for line in reversed(lines):
                if "Sending due task udpate-data-every-30-seconds" in line:
                    time_of_line = re.findall(r'\d{1,4}-\d{1,2}-\d{1,2}\b \d{1,2}:\d{1,2}:\d{1,2}', line)
                    time_of_line = dt.strptime(time_of_line[0], '%Y-%m-%d %H:%M:%S')
                    if time_beat_restarted < time_of_line:
                        _log.info("tyr_beat is up and running")
                        new_time_is_found = True
                        break
        # tyr_beat is malfunctioned, need to delete and re-download
        if not new_time_is_found:
            # stop all containers
            _log.info("Stopping and removing all containers to pull fresh copy of tyr_beat container")
            stop_all_containers(docker_client)

            # delete container and image
            beat_con = docker_client.containers.list(all=True, filters={"name": "beat"})[0]
            beat_image = docker_client.images.list(name="navitia/tyr-beat")[0]
            beat_con_name = beat_con.name
            beat_image_id = beat_image.id
            beat_con.remove()
            _log.info("%s container is removed", beat_con_name)
            docker_client.images.remove(beat_image.id)
            _log.info("%s image is removed", beat_image_id)

            # re-run navitia docker-compose which re-downloads the tyr_beat container
            _log.info("Restarting docker with %s coverage",
                      coverage_name)
            start_navitia_with_single_coverage(navitia_docker_compose_file_path, navitia_docker_compose_file_name,
                                               coverage_name, True)
        # removing the log file
        os.remove("tyr_beat_output.txt")

    else:
        _log.info("Validated tyr_beat is up and running")


def is_aws_machine():
    """
    Checks whether the machine is AWS EC2 instance or not
    :return:
    """
    try:
        r = requests.get("http://169.254.169.254/latest/dynamic/instance-identity/document")
        if r.json() is not None:
            return True
        else:
            return False
    except requests.exceptions.ConnectionError:
        return False


if is_aws_machine():
    import send_email


def send_log_to_email(subject, message):
    """
    Send an e-mail with user-defined subject and message. the e-mail is attached with logs of this script
    :param subject:
    :param message:
    :return: Whether the e-mail was sent successfully
    """
    # Change to root before trying to send logs
    root_path = Path.home() / "TransitAnalystIsrael" / "root"
    os.chdir(root_path.as_posix())
    logs_path = root_path / "logs"
    if not os.path.isdir(logs_path ):
        _log.error("%s isn't the logs directory. Please fix log directory as in code")
    path = logs_path  / '*'
    list_of_files = glob.glob(str(path))  # * means all if need specific format then *.csv
    attached_file = max(list_of_files, key=os.path.getctime)
    return send_email.create_msg_and_send_email(subject, message, attached_file)



def createProgressBar(file_size, action='Downloading: '):
    """
    Craeting a progress bar for continious tasks like downloading file or processing data
    :param file_size: the total size of the file to set the 100% of the bar
    :param action: type of action for the progress bar description, default is "Downloading: "
    :return: a progress bar object
    """
    widgets = [action, progressbar.Percentage(), ' ',
               progressbar.Bar(marker='#', left='[', right=']'),
               ' ', progressbar.ETA(), ' ', progressbar.FileTransferSpeed()]
    # We're increasing the file suze by 10% because sometimes the file split causes cahnges in total size
    pbar = progressbar.ProgressBar(widgets=widgets, maxval=(file_size*1.1))
    pbar.start()
    return pbar


def file_write_update_progress_bar(data, dest_file, pbar, size_iterator):
    """
    Call back for writing fetched or processed data from FTP while updating the progress bar
    """
    dest_file.write(data)
    pbar.update(size_iterator)



def get_gtfs_list_from_omd():
    """
    :return: List of dates indicating different versions of GTFS by starting date
    """
    # _log.info("Retrieving list of available GTFS versions from OpenMobilityData")
    url="https://api.transitfeeds.com/v1/getFeedVersions?key=5bbfcb92-9c9f-4569-9359-0edc6e765e9f&feed=ministry-of-transport-and-road-safety%2F820&page=1&limit=500&err=1&warn=1"
    r = requests.get(url, stream=True)
    response = r.json()
    print(response.status)

