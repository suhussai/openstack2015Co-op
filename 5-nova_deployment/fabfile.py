from __future__ import with_statement
from fabric.api import *
from fabric.decorators import with_settings
from fabric.context_managers import cd
from fabric.colors import green, red
from fabric.contrib.files import append
import logging
import string

import sys
sys.path.append('../global_config_files')
import env_config



############################ Config ########################################

env.roledefs = env_config.roledefs
passwd = env_config.passwd

admin_openrc = "../global_config_files/admin-openrc.sh"

demo_openrc = "../global_config_files/demo-openrc.sh"

etc_nova_config_file = "/etc/nova/nova.conf"


def sudo_log(command):
    output = sudo(command)
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

def run_log(command):
    output = run(command)
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

# logging setup

log_file = 'nova_deployment.log'
env_config.setupLoggingInFabfile(log_file)
# logfilename = env_config.log_location + log_file

# if log_file not in local('ls ' + env_config.log_location,capture=True):
#     # file doesn't exist yet; create it
#     local('touch ' + logfilename,capture=True)
#     local('chmod 644 ' + logfilename,capture=True)

# logging.basicConfig(filename=logfilename,level=logging.DEBUG,format=env_config.log_format)
# # set paramiko logging to only output warnings
# logging.getLogger("paramiko").setLevel(logging.WARNING)


    
################### General functions ########################################

def get_parameter(config_file, section, parameter):
    crudini_command = "crudini --get {} {} {}".format(config_file, section, parameter)
    return local(crudini_command, capture=True)
#    return sudo_log(crudini_command)

def set_parameter(config_file, section, parameter, value):
    crudini_command = "crudini --set {} {} {} {}".format(config_file, section, parameter, value)
    sudo_log(crudini_command)


def setup_nova_database_on_controller(NOVA_DBPASS):
    mysql_commands = "CREATE DATABASE IF NOT EXISTS nova;"
    mysql_commands = mysql_commands + " GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'localhost' IDENTIFIED BY '{}';".format(NOVA_DBPASS)
    mysql_commands = mysql_commands + " GRANT ALL PRIVILEGES ON nova.* TO 'nova'@'%' IDENTIFIED BY '{}';".format(NOVA_DBPASS)

    
    print("mysql commands are: " + mysql_commands)
    sudo_log('echo "{}" | mysql -u root'.format(mysql_commands))
    


def setup_nova_keystone_on_controller(NOVA_PASS):
    source_command = "source admin-openrc.sh"
    with prefix(source_command):

        if 'nova' not in sudo("keystone user-list"):
            sudo_log("keystone user-create --name nova --pass {}".format(NOVA_PASS))
            sudo_log("keystone user-role-add --user nova --tenant service --role admin")
        else:
            logging.debug('User nova already in user list',extra=log_dict)

        if 'nova' not in sudo("keystone service-list"):
            sudo_log("keystone service-create --name nova --type compute --description 'OpenStack Compute'")
        else:
            logging.debug('Service nova already in service list',extra=log_dict)

        if '8774' not in sudo("keystone endpoint-list"):
            sudo_log("keystone endpoint-create --service-id $(keystone service-list | awk '/ compute / {print $2}') --publicurl http://controller:8774/v2/%\(tenant_id\)s  --internalurl http://controller:8774/v2/%\(tenant_id\)s --adminurl http://controller:8774/v2/%\(tenant_id\)s --region regionOne")
        else:
            logging.debug('Endpoint 8774 already in endpoint list',extra=log_dict)
    
def setup_nova_config_files_on_controller(NOVA_PASS, NOVA_DBPASS, RABBIT_PASS, CONTROLLER_MANAGEMENT_IP):
    installation_command = "yum install -y openstack-nova-api openstack-nova-cert openstack-nova-conductor openstack-nova-console openstack-nova-novncproxy openstack-nova-scheduler python-novaclient"
    sudo_log(installation_command)
    
    set_parameter(etc_nova_config_file, 'database', 'connection', 'mysql://nova:{}@controller/nova'.format(NOVA_DBPASS))

    set_parameter(etc_nova_config_file, 'DEFAULT', 'rpc_backend', 'rabbit')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'rabbit_host', 'controller')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'rabbit_password', RABBIT_PASS)

    set_parameter(etc_nova_config_file, 'DEFAULT', 'auth_strategy', 'keystone')

    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'auth_uri', 'http://controller:5000/v2.0')
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'identity_uri', 'http://controller:35357') 
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_tenant_name', 'service') 
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_user', 'nova')   
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_password', NOVA_PASS)   

    #CHECK IF WE NEED TO:
    # "Comment out any auth_host, auth_port, and auth_protocol options because the identity_uri option replaces them." -- manual
    #

    set_parameter(etc_nova_config_file, 'DEFAULT', 'my_ip', CONTROLLER_MANAGEMENT_IP)
    set_parameter(etc_nova_config_file, 'DEFAULT', 'vncserver_listen', CONTROLLER_MANAGEMENT_IP)
    set_parameter(etc_nova_config_file, 'DEFAULT', 'vncserver_proxyclient_address', CONTROLLER_MANAGEMENT_IP)


    set_parameter(etc_nova_config_file, 'glance', 'host', 'controller')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'verbose', 'True')





def populate_database_on_controller():
    sudo_log("su -s /bin/sh -c 'nova-manage db sync' nova")

def start_nova_services_on_controller():
    enable_all = "systemctl enable openstack-nova-api.service openstack-nova-cert.service openstack-nova-consoleauth.service openstack-nova-scheduler.service openstack-nova-conductor.service openstack-nova-novncproxy.service"

    start_all = "systemctl start openstack-nova-api.service openstack-nova-cert.service openstack-nova-consoleauth.service openstack-nova-scheduler.service openstack-nova-conductor.service openstack-nova-novncproxy.service"
    
    sudo_log(enable_all)
    sudo_log(start_all)

def download_packages():
    # make sure we have crudini
    sudo_log('yum install -y crudini')



def setup_nova_config_files_on_compute(NOVA_PASS, NOVA_DBPASS, RABBIT_PASS, NETWORK_MANAGEMENT_IP):



    sudo_log('yum install -y openstack-nova-compute sysfsutils')
    
    set_parameter(etc_nova_config_file, 'DEFAULT', 'rpc_backend', 'rabbit')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'rabbit_host', 'controller')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'rabbit_password', RABBIT_PASS)

    set_parameter(etc_nova_config_file, 'DEFAULT', 'auth_strategy', 'keystone')

    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'auth_uri', 'http://controller:5000/v2.0')
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'identity_uri', 'http://controller:35357') 
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_tenant_name', 'service') 
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_user', 'nova')   
    set_parameter(etc_nova_config_file, 'keystone_authtoken', 'admin_password', NOVA_PASS)   

    #CHECK IF WE NEED TO:
    # "Comment out any auth_host, auth_port, and auth_protocol options because the identity_uri option replaces them." -- manual
    #

    set_parameter(etc_nova_config_file, 'DEFAULT', 'my_ip', NETWORK_MANAGEMENT_IP)

    set_parameter(etc_nova_config_file, 'DEFAULT', 'vnc_enabled', 'True')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'vncserver_listen', '0.0.0.0')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'vncserver_proxyclient_address', NETWORK_MANAGEMENT_IP)
    set_parameter(etc_nova_config_file, 'DEFAULT', 'novncproxy_base_url', 'http://controller:6080/vnc_auto.html')


    set_parameter(etc_nova_config_file, 'glance', 'host', 'controller')
    set_parameter(etc_nova_config_file, 'DEFAULT', 'verbose', 'True')

    hardware_accel_check()

def hardware_accel_check():
    with settings(warn_only=True):
        output = sudo_log("egrep -c '(vmx|svm)' /proc/cpuinfo")    

    if int(output) < 1:
        # we need to do more configuration
        set_parameter(etc_nova_config_file, 'libvirt', 'virt_type', 'qemu')

def start_services_on_compute():
    sudo_log("systemctl enable libvirtd.service openstack-nova-compute.service")
    sudo_log("systemctl start libvirtd.service openstack-nova-compute.service")


@roles('compute')
def setup_nova_on_compute():

    # dictionary for logging formatting
    global log_dict
    log_dict = {'host_string':env.host_string,'role':'compute'}

    download_packages()
    put(admin_openrc)

    # variable setup

    # NOVA_DBPASS = get_parameter(env_config.global_config_file,'mysql','NOVA_DBPASS')
    # NOVA_PASS = get_parameter(env_config.global_config_file,'keystone','NOVA_PASS')
    # RABBIT_PASS = get_parameter(env_config.global_config_file,'rabbitmq', 'RABBIT_PASS')
    NETWORK_MANAGEMENT_IP = env_config.networkManagement['IPADDR']

    setup_nova_config_files_on_compute(passwd['NOVA_PASS'], passwd['NOVA_DBPASS'], passwd['RABBIT_PASS'], NETWORK_MANAGEMENT_IP)        
    start_services_on_compute()
    

@roles('controller')   
def setup_nova_on_controller():
    
    # dictionary for logging formatting
    global log_dict
    log_dict = {'host_string':env.host_string,'role':'controller'}

    host_command = 'sudo_log -- sh -c "{}"'.format("echo '{}' >> /etc/hosts".format("{}        controller".format(env.host))) 
    #    sudo_log(host_command)
    
    
    # fixing bind-address on /etc/my.cnf
    
    # bindCommand = "sed -i.bak 's/^\(bind-address=\).*/\1 {} /' /etc/my.cnf".format(env.host)
    bindCommand = "sed -i '/bind-address/s/=.*/={}/' /etc/my.cnf".format(env.host)
    #    sudo_log(bindCommand)
    
    #    sudo_log("systemctl restart mariadb")
    
    download_packages()
    put(admin_openrc)
    
    # variable setup
    # NOVA_DBPASS = get_parameter(env_config.global_config_file,'mysql','NOVA_DBPASS')
    # NOVA_PASS = get_parameter(env_config.global_config_file,'keystone','NOVA_PASS')
    # RABBIT_PASS = get_parameter(env_config.global_config_file,'rabbitmq', 'RABBIT_PASS')
    CONTROLLER_MANAGEMENT_IP = env_config.controllerManagement['IPADDR']

    # setup nova database
    setup_nova_database_on_controller(passwd['NOVA_DBPASS'])
    setup_nova_keystone_on_controller(passwd['NOVA_PASS'])

    setup_nova_config_files_on_controller(passwd['NOVA_PASS'], passwd['NOVA_DBPASS'], passwd['RABBIT_PASS'], CONTROLLER_MANAGEMENT_IP)
    populate_database_on_controller()
    start_nova_services_on_controller()

################### Deployment ########################################

def deploy():
    execute(setup_nova_on_controller)
    execute(setup_nova_on_compute)

######################################## TDD #########################################



@roles('controller')
def verify():

    # dictionary for logging format
    global log_dict
    log_dict = {'host_string':env.host_string, 'role':'controller'}

    source_command = "source admin-openrc.sh"
    with prefix(source_command):
        sudo_log("nova service-list")
        sudo_log("nova image-list")


def tdd():
    with settings(warn_only=True):
        # to be done on the controller node
        execute(verify)


