from __future__ import with_statement
from fabric.api import *
from fabric.decorators import with_settings
from fabric.context_managers import cd
from fabric.colors import green, red, blue
import string
import logging
import subprocess

import sys
sys.path.append('..')
import env_config
from myLib import runCheck, createDatabaseScript, set_parameter
from myLib import align_y, align_n, keystone_check, database_check, saveConfigFile


############################ Config ########################################

env.roledefs = env_config.roledefs
passwd = env_config.passwd

# define host config file locations
neutron_conf = '/etc/neutron/neutron.conf'
ml2_conf_file = '/etc/neutron/plugins/ml2/ml2_conf.ini'
nova_conf = '/etc/nova/nova.conf'
l3_agent_file = '/etc/neutron/l3_agent.ini'
dhcp_agent_file = '/etc/neutron/dhcp_agent.ini' 
metadata_agent_file = '/etc/neutron/metadata_agent.ini'
sysctl_conf = '/etc/sysctl.conf'

# get database script
database_script = createDatabaseScript('neutron',passwd['NEUTRON_DBPASS'])

# global variable to be used in the TDD functions
status = str()

######################### Deployment ########################################

# CONTROLLER

def create_neutron_database():

    # send the commands to mysql client
    msg = "Create MySQL database for neutron"
    runCheck(msg, '''echo "{}" | mysql -u root -p{}'''.format(database_script, env_config.passwd['ROOT_SECRET']))

def setup_keystone_controller():
    """
    Set up Keystone credentials for Neutron

    Create (a) a user and a service called 'neutron', and 
    (b) an endpoint for the 'neutron' service
    """

    # get credentials
    with prefix(env_config.admin_openrc):

        # check if user neutron has been created and if not, create it
        if 'neutron' not in run("keystone user-list"):
            # create the neutron user in keystone
            msg = "Create neutron user"
            runCheck(msg, 'keystone user-create --name neutron --pass {}'.format(passwd['NEUTRON_PASS']))
            msg = "Add the admin role to the neutron user"
            runCheck(msg, 'keystone user-role-add --user neutron --tenant service --role admin')
        else:
            print blue('\t\tneutron is already a user. Do nothing')

        # check if service neutron has been created and if not, create it
        if 'neutron' not in run("keystone service-list"):
            msg = "Create the neutron service entity"
            runCheck(msg, 'keystone service-create --name neutron --type network --description "OpenStack Networking"')
        else:
            print blue('\t\tneutron is already a service. Do nothing')

        # check if a 9696 endpoint already exists and if not, create one
        if 'http://controller:9696' not in run("keystone endpoint-list"):
            msg =  "Create the networking service API endpoints"
            runCheck(msg, 'keystone endpoint-create ' + \
                    "--service-id $(keystone service-list | awk '/ network / {print $2}') " + \
                    "--publicurl http://controller:9696 " + \
                    "--adminurl http://controller:9696 " + \
                    "--internalurl http://controller:9696 " + \
                    "--region regionOne")
        else:
            print blue('\t\t9696 is already an endpoint. Do nothing')

def configure_networking_server_component():
    # configure neutron.conf with crudini
 
    # make a backup
    run('cp {} {}.back12'.format(neutron_conf,neutron_conf))

    # configure database access
    parameter = 'mysql://neutron:{}@controller/neutron'.format(passwd['NEUTRON_DBPASS'])
    set_parameter(neutron_conf,'database','connection',parameter)

    # configure RabbitMQ access
    set_parameter(neutron_conf,'DEFAULT','rpc_backend','rabbit')
    set_parameter(neutron_conf,'DEFAULT','rabbit_host','controller')
    set_parameter(neutron_conf,'DEFAULT','rabbit_password',passwd['RABBIT_PASS'])

    # configure Identity service access

    set_parameter(neutron_conf,'DEFAULT','auth_strategy','keystone')
    set_parameter(neutron_conf,'keystone_authtoken','auth_uri','http://controller:5000/v2,0')
    set_parameter(neutron_conf,'keystone_authtoken','identity_uri','http://controller:35357')
    set_parameter(neutron_conf,'keystone_authtoken','admin_tenant_name','service')
    set_parameter(neutron_conf,'keystone_authtoken','admin_user','neutron')
    set_parameter(neutron_conf,'keystone_authtoken','admin_password',passwd['NEUTRON_PASS'])

    # enable ML2 plugin

    set_parameter(neutron_conf,'DEFAULT','core_plugin','ml2')
    set_parameter(neutron_conf,'DEFAULT','service_plugins','router')
    set_parameter(neutron_conf,'DEFAULT','allow_overlapping_ips','True')

    # set Neutron to notify Nova of of topology changes
    # get service tenant id
    with prefix(env_config.admin_openrc):
        nova_admin_tenant_id = run('keystone tenant-list | grep service | cut -d\| -f2')

    if nova_admin_tenant_id:
        # if tenant service doesn't exist, this variable will be empty
        set_parameter(neutron_conf,'DEFAULT','nova_admin_tenant_id',nova_admin_tenant_id)


    set_parameter(neutron_conf,'DEFAULT','notify_nova_on_port_status_changes','True')
    set_parameter(neutron_conf,'DEFAULT','notify_nova_on_port_data_changes','True')
    set_parameter(neutron_conf,'DEFAULT','nova_url','http://controller:8774/v2')
    set_parameter(neutron_conf,'DEFAULT','nova_admin_auth_url','http://controller:35357/v2.0')
    set_parameter(neutron_conf,'DEFAULT','nova_region_name','regionOne')
    set_parameter(neutron_conf,'DEFAULT','nova_admin_username','nova')
    set_parameter(neutron_conf,'DEFAULT','nova_admin_password',passwd['NOVA_PASS'])

    # turn on verbose logging
    set_parameter(neutron_conf,'DEFAULT','verbose','True')

def configure_ML2_plugin_general():
    # The ML2 plug-in uses the Open vSwitch (OVS) mechanism (agent) to build the virtual 
    # networking framework for instances. However, the controller node does not need the OVS 
    # components because it does not handle instance network traffic.


    set_parameter(ml2_conf_file,'ml2','type_drivers','flat,gre')
    set_parameter(ml2_conf_file,'ml2','tenant_network_types','gre')
    set_parameter(ml2_conf_file,'ml2','mechanism_drivers','openvswitch')

    set_parameter(ml2_conf_file,'ml2_type_gre','tunnel_id_ranges','1:1000')

    set_parameter(ml2_conf_file,'securitygroup','enable_security_group','True')
    set_parameter(ml2_conf_file,'securitygroup','enable_ipset','True')
    set_parameter(ml2_conf_file,'securitygroup','firewall_driver',\
            'neutron.agent.linux.iptables_firewall.OVSHybridIptablesFirewallDriver')

def restart_nova_controller():
    # Restart nova
    msg = "Restart Nova services"
    runCheck(msg, 'systemctl restart openstack-nova-api.service openstack-nova-scheduler.service' + \
              ' openstack-nova-conductor.service')



def configure_nova_to_use_neutron():

    set_parameter(nova_conf,'DEFAULT','network_api_class','nova.network.neutronv2.api.API')
    set_parameter(nova_conf,'DEFAULT','security_group_api','neutron')
    set_parameter(nova_conf,'DEFAULT','linuxnet_interface_driver','nova.network.linux_net.LinuxOVSInterfaceDriver')
    set_parameter(nova_conf,'DEFAULT','firewall_driver','nova.virt.firewall.NoopFirewallDriver')

    set_parameter(nova_conf,'neutron','url','http://controller:9696')
    set_parameter(nova_conf,'neutron','auth_strategy','keystone')
    set_parameter(nova_conf,'neutron','admin_auth_url','http://controller:35357/v2.0')
    set_parameter(nova_conf,'neutron','admin_tenant_name','service')
    set_parameter(nova_conf,'neutron','admin_username','neutron')
    set_parameter(nova_conf,'neutron','admin_password',passwd['NEUTRON_PASS'])

@roles('controller')
def installPackagesController():

    msg = "Install Neutron packages on controller"
    runCheck(msg, 
            'yum -y install '
            'openstack-neutron '
            'openstack-neutron-ml2 '
            'python-neutronclient '
            'which')



@roles('controller')
def controller_deploy():

    create_neutron_database()

    setup_keystone_controller()

    installPackagesController()
    
    configure_networking_server_component()

    configure_ML2_plugin_general()

    configure_nova_to_use_neutron()

    restart_nova_controller()

    # The Networking service initialization scripts expect a symbolic link /etc/neutron/plugin.ini 
    # pointing to the ML2 plug-in configuration file, /etc/neutron/plugins/ml2/ml2_conf.ini. 
    # If this symbolic link does not exist, create it
    if 'plugin.ini' not in run('ls /etc/neutron'):
        msg = "Create symbolic link to ml2 conf file"
        runCheck(msg, 'ln -s /etc/neutron/plugins/ml2/ml2_conf.ini /etc/neutron/plugin.ini')

    msg = "Populate the database for neutron"
    runCheck(msg, 'su -s /bin/sh -c "neutron-db-manage --config-file /etc/neutron/neutron.conf ' + \
              '--config-file /etc/neutron/plugins/ml2/ml2_conf.ini upgrade juno" neutron')

    # Restart nova
    msg = "Restart Nova services"
    runCheck(msg, 'systemctl restart openstack-nova-api.service openstack-nova-scheduler.service' + \
              ' openstack-nova-conductor.service')

    msg = "Enable Neutron service"
    runCheck(msg, 'systemctl enable neutron-server.service')
    msg = "Enable Neutron service"
    runCheck(msg, 'systemctl start neutron-server.service')
    

# NETWORK

def configure_the_Networking_common_components():

    # make a backup
    run('cp {} {}.back12'.format(neutron_conf,neutron_conf))

    # configure RabbitMQ access
    set_parameter(neutron_conf,'DEFAULT','rpc_backend','rabbit')
    set_parameter(neutron_conf,'DEFAULT','rabbit_host','controller')
    set_parameter(neutron_conf,'DEFAULT','rabbit_password',passwd['RABBIT_PASS'])

    # configure Identity service access

    set_parameter(neutron_conf,'DEFAULT','auth_strategy','keystone')
    set_parameter(neutron_conf,'keystone_authtoken','auth_uri','http://controller:5000/v2.0')
    set_parameter(neutron_conf,'keystone_authtoken','identity_uri','http://controller:35357')
    set_parameter(neutron_conf,'keystone_authtoken','admin_tenant_name','service')
    set_parameter(neutron_conf,'keystone_authtoken','admin_user','neutron')
    set_parameter(neutron_conf,'keystone_authtoken','admin_password',passwd['NEUTRON_PASS'])

    # enable ML2 plugin

    set_parameter(neutron_conf,'DEFAULT','core_plugin','ml2')
    set_parameter(neutron_conf,'DEFAULT','service_plugins','router')
    set_parameter(neutron_conf,'DEFAULT','allow_overlapping_ips','True')
    set_parameter(neutron_conf,'DEFAULT','verbose','True')

def configure_ML2_plug_in_network():
    
    # most of the configuration is the same as the controller
    configure_ML2_plugin_general()

    # configure the external flat provider network 
    set_parameter(ml2_conf_file,'ml2_type_flat','flat_networks','external')

    # configure the external flat provider network 
    set_parameter(ml2_conf_file,'ovs','enable_tunneling','True')
    set_parameter(ml2_conf_file,'ovs','bridge_mappings','external:br-ex')
    local_ip = env_config.nicDictionary['network']['tnlIPADDR']
    set_parameter(ml2_conf_file,'ovs','local_ip',local_ip)

    # enable GRE tunnels 
    set_parameter(ml2_conf_file,'agent','tunnel_types','gre')

def configure_Layer3_agent():

    set_parameter(l3_agent_file,"DEFAULT","interface_driver","neutron.agent.linux.interface.OVSInterfaceDriver")
    set_parameter(l3_agent_file,"DEFAULT","use_namespaces","True")
    set_parameter(l3_agent_file,"DEFAULT","external_network_bridge","br-ex")
    set_parameter(l3_agent_file,"DEFAULT","router_delete_namespaces","True")
    set_parameter(l3_agent_file,"DEFAULT","verbose","True")

def configure_DHCP_agent():

    set_parameter(dhcp_agent_file,"DEFAULT","interface_driver","neutron.agent.linux.interface.OVSInterfaceDriver")
    set_parameter(dhcp_agent_file,"DEFAULT","dhcp_driver","neutron.agent.linux.dhcp.Dnsmasq")
    set_parameter(dhcp_agent_file,"DEFAULT","use_namespaces","True")
    set_parameter(dhcp_agent_file,"DEFAULT","dhcp_delete_namespaces","True")
    set_parameter(dhcp_agent_file,"DEFAULT","verbose","True")

@roles('controller')
def configure_metadata_proxy_on_controller():
    # to configure the metadata agent, some changes need to be made
    # on the controller node

    set_parameter(nova_conf,'neutron','service_metadata_proxy','True')
    set_parameter(nova_conf,'neutron','metadata_proxy_shared_secret',passwd['METADATA_SECRET'])

    msg = "Restart Nova service"
    runCheck(msg, "systemctl restart openstack-nova-api.service")


def configure_metadata_agent():

    set_parameter(metadata_agent_file,'DEFAULT','auth_url','http://controller:5000/v2.0')
    set_parameter(metadata_agent_file,'DEFAULT','auth_region','regionOne')
    set_parameter(metadata_agent_file,'DEFAULT','admin_tenant_name','service')
    set_parameter(metadata_agent_file,'DEFAULT','admin_user','neutron')
    set_parameter(metadata_agent_file,'DEFAULT','nova_metadata_ip','controller')
    set_parameter(metadata_agent_file,'DEFAULT','admin_password',passwd['NEUTRON_PASS'])
    set_parameter(metadata_agent_file,'DEFAULT','metadata_proxy_shared_secret',passwd['METADATA_SECRET'])
    set_parameter(metadata_agent_file,'DEFAULT','verbose','True')

    execute(configure_metadata_proxy_on_controller)

def configure_Open_vSwitch_service():

    msg = 'Enable OpenvSwitch service'
    runCheck(msg, "systemctl enable openvswitch.service")
    msg = 'Start OpenvSwitch service'
    runCheck(msg, "systemctl start openvswitch.service")

    # for testing
    # run("ovs-vsctl del-br br-ex")

    # add br-ex bridge
    if 'br-ex' not in run("ovs-vsctl list-br"):
        msg = 'Create bridge br-ex'
        runCheck(msg, "ovs-vsctl add-br br-ex")

        interface_name = env_config.nicDictionary['network']['extDEVICE']
        msg = 'Add port to br-ex'
        runCheck(msg, "ovs-vsctl --log-file=/home/uadm/ovslog add-port br-ex '{}'".format(interface_name))
    else:
        print blue('br-ex already created. Do nothing')

@roles('network')
def installPackagesNetwork():

    msg = "Install Neutron packages on network"
    runCheck(msg, 
            "yum -y install "
            "openstack-neutron "
            "openstack-neutron-ml2 "
            "openstack-neutron-openvswitch",
            )

@roles('network')
def network_deploy():

    # edit sysctl.conf

    set_parameter(sysctl_conf,"''",'net.ipv4.ip_forward','1')
    set_parameter(sysctl_conf,"''",'net.ipv4.conf.all.rp_filter','0')
    set_parameter(sysctl_conf,"''",'net.ipv4.conf.default.rp_filter','0')

    msg = "Implement changes on sysctl"
    runCheck(msg, "sysctl -p")

    installPackagesNetwork()
    
    # configuration 

    configure_the_Networking_common_components()

    configure_ML2_plug_in_network()

    configure_Layer3_agent()

    configure_DHCP_agent()

    configure_metadata_agent()

    configure_Open_vSwitch_service()

    # finalize installation

    # The Networking service initialization scripts expect a symbolic link /etc/neutron/plugin.ini 
    # pointing to the ML2 plug-in configuration file, /etc/neutron/plugins/ml2/ml2_conf.ini. 
    # If this symbolic link does not exist, create it
    if 'plugin.ini' not in run('ls /etc/neutron'):
        msg = "Create symbolic link to ml2 conf file"
        runCheck(msg, 'ln -s /etc/neutron/plugins/ml2/ml2_conf.ini /etc/neutron/plugin.ini')

    # Due to a packaging bug, the Open vSwitch agent initialization script explicitly looks for 
    # the Open vSwitch plug-in configuration file rather than a symbolic link /etc/neutron/plugin.ini pointing to the ML2 
    # plug-in configuration file. Run the following commands to resolve this issue:
    run("cp /usr/lib/systemd/system/neutron-openvswitch-agent.service " + \
            "/usr/lib/systemd/system/neutron-openvswitch-agent.service.orig")
    run("sed -i 's,plugins/openvswitch/ovs_neutron_plugin.ini,plugin.ini,g' " + \
            "/usr/lib/systemd/system/neutron-openvswitch-agent.service")

    # initialize services
    msg = "Enable neutron services"
    run("systemctl enable neutron-openvswitch-agent.service neutron-l3-agent.service " +  \
              "neutron-dhcp-agent.service neutron-metadata-agent.service " + \
                "neutron-ovs-cleanup.service")
    msg = "Start neutron services"
    run("systemctl start neutron-openvswitch-agent.service neutron-l3-agent.service " + \
              "neutron-dhcp-agent.service neutron-metadata-agent.service")


# COMPUTE

def configure_ML2_plug_in_compute():
    
    # most of the configuration is the same as the controller
    configure_ML2_plugin_general()

    # configure the external flat provider network 
    set_parameter(ml2_conf_file,'ovs','enable_tunneling','True')
    local_ip = env_config.nicDictionary['compute1']['tnlIPADDR']
    set_parameter(ml2_conf_file,'ovs','local_ip',local_ip)

    # enable GRE tunnels 
    set_parameter(ml2_conf_file,'agent','tunnel_types','gre')

@roles('compute')
def installPackagesCompute():

    msg = "Install Neutron packages on " + env.host
    runCheck(msg, "yum -y install "
            "openstack-neutron-ml2 "
            "openstack-neutron-openvswitch",
            )

@roles('compute')
def compute_deploy():
    
    # edit sysctl.conf

    set_parameter(sysctl_conf,"''",'net.ipv4.conf.all.rp_filter','0')
    set_parameter(sysctl_conf,"''",'net.ipv4.conf.default.rp_filter','0')

    msg = "Implement changes on sysctl on compute node " + env.host
    runCheck(msg, "sysctl -p")

    installPackagesCompute()

    # configuration

    configure_the_Networking_common_components() # same as networking

    configure_ML2_plug_in_compute()

    configure_nova_to_use_neutron()

    msg = 'Enable Open vSwitch'
    runCheck(msg, 'systemctl enable openvswitch.service')
    msg = 'Start Open vSwitch'
    runCheck(msg, 'systemctl start openvswitch.service')

    # finalize installation

    # The Networking service initialization scripts expect a symbolic link /etc/neutron/plugin.ini 
    # pointing to the ML2 plug-in configuration file, /etc/neutron/plugins/ml2/ml2_conf.ini. 
    # If this symbolic link does not exist, create it
    if 'plugin.ini' not in run('ls /etc/neutron'):
        msg = 'Create a symbolic link to Open vSwitch\'s conf file'
        runCheck(msg, 'ln -s /etc/neutron/plugins/ml2/ml2_conf.ini /etc/neutron/plugin.ini')

    # Due to a packaging bug, the Open vSwitch agent initialization script explicitly looks for 
    # the Open vSwitch plug-in configuration file rather than a symbolic link /etc/neutron/plugin.ini pointing to the ML2 
    # plug-in configuration file. Run the following commands to resolve this issue:
    msg = 'Chenge Open vSwitch to look for a symbolic link to to the ML2 conf file'
    run("cp /usr/lib/systemd/system/neutron-openvswitch-agent.service " + \
            "/usr/lib/systemd/system/neutron-openvswitch-agent.service.orig")
    run("sed -i 's,plugins/openvswitch/ovs_neutron_plugin.ini,plugin.ini,g' " + \
            "/usr/lib/systemd/system/neutron-openvswitch-agent.service")

    msg = 'Restart Nova service'
    runCheck(msg, "systemctl restart openstack-nova-compute.service")

    msg = 'Enable Open vSwitch'
    runCheck(msg, 'systemctl enable neutron-openvswitch-agent.service')
    msg = 'Start Open vSwitch'
    runCheck(msg, 'systemctl start neutron-openvswitch-agent.service')
    msg = 'Restart Open vSwitch'
    runCheck(msg, 'systemctl restart neutron-openvswitch-agent.service')

# INITIAL NETWORK

@roles('controller')
def createExtNet():
    
    with prefix(env_config.admin_openrc):

        if 'ext-net' in run('neutron net-list'):
            msg = 'Ext-net already created'
            print msg
        else:
            msg = 'create external network on network node'
            runCheck(msg,
                    'neutron net-create ext-net '
                    '--router:external True '
                    '--provider:physical_network external '
                    '--provider:network_type flat'
                    )

        msg = 'Restart Neutron service'
        runCheck(msg, 'systemctl restart neutron-server.service')

@roles('controller')
def createExtSubnet():

    start = env_config.ext_subnet['start']
    end = env_config.ext_subnet['end']
    gateway = env_config.ext_subnet['gateway']
    cidr = env_config.ext_subnet['cidr']

    with prefix(env_config.admin_openrc):
        if 'ext-subnet' in run('neutron subnet-list'):
            msg = 'ext-subnet already created'
            print msg
        else:
            msg = 'create initial subnet on external net on network node'
            runCheck(msg,
                    'neutron subnet-create ext-net '
                    '--name ext-subnet '
                    '--allocation-pool start={},end={} '.format(start,end)+\
                    '--disable-dhcp '
                    '--gateway {} {}'.format(gateway,cidr)
                    )

        msg = 'Restart Neutron service'
        runCheck(msg, 'systemctl restart neutron-server.service')

@roles('controller')
def createDemoNet():

    with prefix(env_config.demo_openrc):
        if 'demo-net' in run('neutron net-list'):
            msg = 'Demo-net already created'
            print msg
        else:
            msg = 'create initial demo tenant network on network node'
            runCheck(msg, 'neutron net-create demo-net')

        msg = 'Restart Neutron service'
        runCheck(msg, 'systemctl restart neutron-server.service')

@roles('controller')
def createDemoSubnet():

    gateway = env_config.demo_subnet['gateway']
    cidr = env_config.demo_subnet['cidr']

    with prefix(env_config.demo_openrc):
        if 'demo-subnet' in run('neutron subnet-list'):
            msg = 'Demo-subnet already created'
            print msg
        else:
            msg = 'create subnet on demo-net'
            runCheck(msg,
                    'neutron subnet-create demo-net '
                    '--name demo-subnet '
                    '--gateway {} {}'.format(gateway,cidr)
                    )

        msg = 'Restart Neutron service'
        runCheck(msg, 'systemctl restart neutron-server.service')

@roles('controller')
def createDemoRouter():
    
    with prefix(env_config.demo_openrc):
        if 'demo-router' in run('neutron router-list'):
            msg = 'Demo-router already created'
            print msg
        else:
            msg = 'create the demo router'
            runCheck(msg,'neutron router-create demo-router')

            msg = 'attach the demo router to the demo subnet'
            runCheck(msg,
                    'neutron router-interface-add demo-router demo-subnet')

            msg = 'attach the router to the external network '
            'by setting it as the gateway'
            runCheck(msg,'neutron router-gateway-set demo-router ext-net')

        msg = 'Restart Neutron service'
        runCheck(msg, 'systemctl restart neutron-server.service')


@roles('controller')
def createInitialNetwork():
    # Creates a sample network for testing 

    execute(createExtNet)
    execute(createExtSubnet)
    execute(createDemoNet)
    execute(createDemoSubnet)
    execute(createDemoRouter)

def deploy():

    # with settings(warn_only=True):
    execute(controller_deploy)
    execute(network_deploy)
    execute(compute_deploy)
    execute(createInitialNetwork)

######################################## TDD #########################################

@roles('network', 'controller', 'compute')
def createInitialNetworkTdd():

    # this is repeated, need to translate into env_config
    floatingIPStart = env_config.ext_subnet['start']

    msg = "Ping the tenant router gateway from {}".format(env.host)
    runCheck(msg, "ping -c 1 {}".format(floatingIPStart))

@roles('controller')
def controller_tdd():

    # Check loaded extensions to verify launch of neutron
    alias_name_pairs = [('security-group','security-group'),
                        ('l3_agent_scheduler','L3 Agent Scheduler'),
                        ('ext-gw-mode','Neutron L3 Configurable external gateway mode'),
                        ('binding','Port Binding'),
                        ('provider','Provider Network'),
                        ('agent','agent'),
                        ('quotas','Quota management support'),
                        ('dhcp_agent_scheduler','DHCP Agent Scheduler'),
                        ('l3-ha','HA Router extension'),
                        ('multi-provider','Multi Provider Network'),
                        ('external-net','Neutron external network'),
                        ('router','Neutron L3 Router'),
                        ('allowed-address-pairs','Allowed Address Pairs'),
                        ('extraroute','Neutron Extra Route'),
                        ('extra_dhcp_opt','Neutron Extra DHCP opts'),
                        ('dvr','Distributed Virtual Router'),
                        ]

    print 'Checking loaded extensions'
    
    with prefix(env_config.admin_openrc):
        # save ext-list into a file, to avoid running the list command several times
        ext_list = run('neutron ext-list >ext-list',quiet=True)

        if ext_list.return_code != 0:
            print red('Could not run ext-list')
            return 'FAIL'

        for pair in alias_name_pairs:
            alias = pair[0]
            name = run("cat ext-list | grep ' {} ' | cut -d\| -f3".format(alias),quiet=True)
            if pair[1] not in name:
                print align_n("Alias {} should be {}, is {}".format(alias,pair[1],name.strip()))
                status = 'bad'
            else:
                print align_y("Alias {} is {}, as expected".format(alias,name.strip()))

        run('rm ext-list',quiet=True)

@roles('controller')
def verify_neutron_agents(neutron_agents,hostname):
    """
    TDD: verify successful launch of the neutron agents
    """

    with prefix(env_config.admin_openrc):

        # grab the agent list and save it to a file
        run("neutron agent-list >agent-list",quiet=True)

        for agent in neutron_agents:
            agent_line = run("cat agent-list | grep '%s' | grep '%s'" % (agent,hostname),quiet=True)
            if agent_line.return_code != 0:
                print align_n("Neutron agent {} not found in agent-list".format(agent))
                status = 'bad'
            else:
                n_lines = len(agent_line.splitlines())

                if n_lines > 1:
                    print align_n('There\'s more than one agent called ' + agent)
                elif hostname not in agent_line:
                    print align_n('Host for agent %s is not %s' % (agent,hostname))
                    status = 'bad'
                elif ':-)' not in agent_line:
                    print align_n("Status for %s agent is not ':-)'" % agent)
                    status = 'bad'
                else:
                    print align_y("Neutron agent {} OK!".format(agent))

        # remove local file
        run("rm agent-list",quiet=True)

@roles('network')
def network_tdd():
    agents = ['Metadata','Open vSwitch','L3','DHCP']
    return execute(verify_neutron_agents,neutron_agents=agents,hostname='network')

@roles('compute')
def compute_tdd():

    with prefix(env_config.admin_openrc):
        agent = 'Open vSwitch'
        # get list of compute nodes from the hosts config
        list_of_compute_hostnames = [hostname for hostname in env_config.hosts\
                if 'compute' in ''.join(hostname)]

        agent_lines = run('neutron agent-list | grep "%s"' % agent, quiet=True).splitlines()
        for line in agent_lines:
            if ':-)' not in line:
                print align_n('Problem with agent ' + agent)
                status = 'bad'
            else:
                print align_y("Neutron agent %s OK!" % agent)


@roles('controller')
def saveConfigController(status):
    """
    Save locally the config files that exist in the controller node
    """
    saveConfigFile(neutron_conf,status)
    saveConfigFile(ml2_conf_file,status)
    saveConfigFile(nova_conf,status)

@roles('network')
def saveConfigNetwork(status):
    """
    Save locally the config files that exist in the network node
    """
    saveConfigFile(sysctl_conf,status)
    saveConfigFile(neutron_conf,status)
    saveConfigFile(ml2_conf_file,status)
    saveConfigFile(l3_agent_file,status)
    saveConfigFile(dhcp_agent_file,status)
    saveConfigFile(metadata_agent_file,status)

@roles('compute')
def saveConfigCompute(status):
    """
    Save locally the config files that exist in the compute nodes
    """
    saveConfigFile(sysctl_conf,status)
    saveConfigFile(neutron_conf,status)
    saveConfigFile(nova_conf,status)

def tdd():

    # status is initialized as 'good'
    # if any of the tdd functions gets an error,
    # it changes the value to 'bad'
    status = 'good'

    with settings(warn_only=True):

        res = execute(keystone_check,'neutron',roles=['controller'])
        if res.values()[0] == 'FAIL':
            status = 'bad'

        res = execute(database_check,'neutron',roles=['controller'])
        if res.values()[0] == 'FAIL':
            status = 'bad'

        execute(controller_tdd)

        execute(network_tdd)

        execute(compute_tdd)

        execute(createInitialNetworkTdd)

        # save config files
        execute(saveConfigController,status)
        execute(saveConfigNetwork,status)
        execute(saveConfigCompute,status)


