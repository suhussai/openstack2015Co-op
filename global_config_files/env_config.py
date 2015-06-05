import logging 
from subprocess import check_output, call
from fabric.api import run, sudo, env
from fabric.colors import red, green

##################### General functions ######################


def keystone_check():

    def tenant_exists(name):
        if name in sudo("keystone tenant-list | awk '// {print $4}'"):
            print(green(name +" tenant exists"))
            print okay
        else:
            print(red(name +" tenant does NOT exists"))

    def tenant_enabled(name):
        if name in sudo("keystone tenant-list | awk '/" + name + "/ {print $6}'"):
            print(green(name +" tenant enabled"))
            print okay
        else:
            print(red(name +" tenant NOT enabled"))

    def service_exists(name):
        if name in sudo("keystone service-list | awk '// {print$4}'"):
            output = sudo("keystone service-list | awk '/" + name + "/ {print$4}'"):
            print(green(name +" service exists. Type: " + output))
            print okay
        else:
            print(name +" service does NOT exist")


            

    def mysql_it(sql_command):
        mysql_command = "mysql -B --disable-column-names -u root"
        return sudo("""echo "{}" | {}""".format(sql_command, mysql_command))
    
    command = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = 'keystone';"
    if "keystone" not in mysql_it(command):
        print(red("keystone database does not exist"))
        print(red("exiting function"))
        return
        
    def tenant_check(name):
        # checks for existence and whether or not it is enabled
        









def database_check(db):

    # 'OK' message
    okay = '[ ' + green('OK') + ' ]'
        
    def db_exists(db):
        command = "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = '{}';".format(db)
        if db in sudo("""echo "{}" | mysql -u root""".format(command)):
            return True
        else:
            return False
        
    def table_count(db):
        command = "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '{}';".format(db) 
        output = sudo("""echo "{}" | mysql -u root | grep -v "COUNT" """.format(command))
        return int(output)

    if db_exists(db):
        message = "DB " + db + " exists"
        print green(message)
        print okay
        logging.debug(message,extra=log_dict)
    else:
        message = "DB " + db + " does not exist"
        print red(message)
        logging.debug(message,extra=log_dict)

    nbr = table_count(db)
    if nbr > 0:
        message = "table for " + db + " has " + str(nbr) + " entries"
        print green(message)
        print okay
        logging.debug(message,extra=log_dict)
    else:
        message = "table for " + db + " is empty. Nbr of entries : " + str(nbr)
        print red(message)
        logging.debug(message,extra=log_dict)

# Read the values from a node file into a list
def read_nodes(node):
    node_info = open(node, 'r')
    node_string = node_info.read().splitlines()
    # remove comments (lines that have # in the beginning)
    # node_string_stripped = [node_element.strip() for node_element in node_string if node_element[0] != '#']
    node_info.close()
    #print node_string_stripped
    return node_string

# Make a dictionary from a config file with the format "KEY=value" on each 
# line
def read_dict(config_file):
    config_file_info = open(config_file, 'r')
    config_file_without_comments = [line for line in config_file_info.readlines() if line[0] != '#']
    config_file_string = "".join(config_file_without_comments)
    # config_file_string = config_file_info.read().replace('=','\n').splitlines()
    config_file_string = config_file_string.replace('=','\n').splitlines()
    config_file_string_stripped = [config_element.strip() for config_element in config_file_string]
    config_file_dict = dict()
    
    # Make a dictionary from the string from the file with the the first value
    # on a line being the key to the value after the '=' on the same line
    for config_file_key_index in range(0,len(config_file_string_stripped)-1,2):
        config_file_value_index = config_file_key_index + 1
        config_file_dict[config_file_string_stripped[config_file_key_index]] = config_file_string_stripped[config_file_value_index]
    
    config_file_info.close()

    #run("rm -rf %s" % config_file)
    return config_file_dict

# Do a fabric command on the string 'command' and log results
def fabricLog(command,func,log_dict):
    output = func(command)
    if output.return_code != 0:
        logging.error("Problem on command '{}'".format(command),extra=log_dict)
    else:
        for line in output.splitlines():
            # don't log lines that have passwords
            if 'pass' not in line.lower():
                # skip empty lines
                if line != '' or line !='\n':
                    logging.debug(line,extra=log_dict)
    return output


def setupLoggingInFabfile(log_file):
    logfilename = log_location + log_file

    if log_file not in check_output('ls ' + log_location,shell=True):
        # file doesn't exist yet; create it
        call('touch ' + logfilename,shell=True)
        call('chmod 644 ' + logfilename,shell=True)

    logging.basicConfig(filename=logfilename,level=logging.DEBUG,format=log_format)
    # set paramiko logging to only output warnings
    logging.getLogger("paramiko").setLevel(logging.WARNING)

def getRole():
    for role in env.roledefs.keys():
        if env.host_string in env.roledefs[role]:
            return role
    # if none was found
    raise ValueError("Host " + env.hoststring + " not in roledefs")


######################### Global variables ######################

# Variables that can be imported into the env dictionary
hosts = list()
roledefs = dict()

# Get nodes and their roles from the config files
compute_nodes = read_nodes('../global_config_files/compute_nodes')
controller_nodes = read_nodes('../global_config_files/controller_nodes')
network_nodes = read_nodes('../global_config_files/network_nodes')
storage_nodes = read_nodes('../global_config_files/storage_nodes')

hosts = compute_nodes + controller_nodes + network_nodes
roledefs = { 'controller':controller_nodes, 'compute':compute_nodes, 'network':network_nodes, 'storage':storage_nodes }

global_config_file = '../global_config_files/global_config'
global_config_location =  '../global_config_files/'

# LOGGING

#log_location = '/var/log/juno/'
#if not check_output('sudo if [ -e {} ]; then echo found; fi'.format(log_location),shell=True):
#    # location not created; make it
#    call('sudo mkdir -p ' + log_location,shell=True)
#    call('sudo chmod 777 ' + log_location,shell=True)


log_format = '%(asctime)-15s:%(levelname)s:%(host_string)s:%(role)s:\t%(message)s'
log_location = '../var/log/juno/'
if not check_output('if [ -e {} ]; then echo found; fi'.format(log_location),shell=True):
    # location not created yet
    call('mkdir -p ' + log_location,shell=True)
    call('chmod 744 ' + log_location,shell=True)
log_dict = {'host_string':'','role':''} # default value for log_dict


# scripts to be sourced

admin_openrc = global_config_location + 'admin-openrc.sh'
demo_openrc = global_config_location + 'demo-openrc.sh'

# get passwords

global_config_file_lines = check_output("crudini --get --list --format=lines " + global_config_file,shell=True).splitlines()
# drop header
global_config_file_lines = [line.split(' ] ')[1] for line in global_config_file_lines]
# break between parameter and value
pairs = [line.split(' = ') for line in global_config_file_lines]
# make passwd dictionary
passwd = {pair[0].upper():pair[1] for pair in pairs}
