# Copyright 2018 AT&T Intellectual Property.  All other rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import copy
import json
import logging
import pkg_resources
import pprint
import sys

import jsonschema
import netaddr
import yaml

LOG = logging.getLogger(__name__)


class ProcessDataSource():
    def __init__(self, sitetype):
        # Initialize intermediary and save site type
        self._initialize_intermediary()
        self.region_name = sitetype

    @staticmethod
    def _read_file(file_name):
        with open(file_name, 'r') as f:
            raw_data = f.read()
        return raw_data

    def _initialize_intermediary(self):
        self.host_type = {}
        self.data = {
            'network': {},
            'baremetal': {},
            'region_name': '',
            'storage': {},
            'site_info': {},
        }
        self.sitetype = None
        self.genesis_node = None
        self.region_name = None

    def _get_network_subnets(self):
        # Extract subnet information for networks
        LOG.info("Extracting network subnets")
        network_subnets = {}
        for net_type in self.data['network']['vlan_network_data']:
            # One of the type is ingress and we don't want that here
            if (net_type != 'ingress'):
                network_subnets[net_type] = netaddr.IPNetwork(
                    self.data['network']['vlan_network_data'][net_type]
                    ['subnet'])

        LOG.debug("Network subnets:\n{}".format(
            pprint.pformat(network_subnets)))
        return network_subnets

    def _get_genesis_node_details(self):
        # Returns the genesis node details
        LOG.info("Getting Genesis Node Details")
        for racks in self.data['baremetal'].keys():
            rack_hosts = self.data['baremetal'][racks]
            for host in rack_hosts:
                if rack_hosts[host]['type'] == 'genesis':
                    self.genesis_node = rack_hosts[host]
                    self.genesis_node['name'] = host

        LOG.debug("Genesis Node Details:{}".format(
            pprint.pformat(self.genesis_node)))

    def _validate_extracted_data(self, data):
        """ Validates the extracted data from input source.


        It checks wether the data types and data format are as expected.
        The method validates this with regex pattern defined for each
        data type.
        """
        LOG.info('Validating data read from extracted data')
        temp_data = {}
        temp_data = copy.deepcopy(data)

        # Converting baremetal dict to list.
        baremetal_list = []
        for rack in temp_data['baremetal'].keys():
            temp = [{k: v} for k, v in temp_data['baremetal'][rack].items()]
            baremetal_list = baremetal_list + temp

        temp_data['baremetal'] = baremetal_list
        schema_dir = pkg_resources.resource_filename('spyglass', 'schemas/')
        schema_file = schema_dir + "data_schema.json"
        json_data = json.loads(json.dumps(temp_data))
        with open(schema_file, 'r') as f:
            json_schema = json.load(f)

        try:
            # Suppressing writing of data2.json. Can use it for debugging
            with open('data2.json', 'w') as outfile:
                json.dump(temp_data, outfile, sort_keys=True, indent=4)
            jsonschema.validate(json_data, json_schema)
        except jsonschema.exceptions.ValidationError as e:
            LOG.error("Validation Error")
            LOG.error("Message:{}".format(e.message))
            LOG.error("Validator_path:{}".format(e.path))
            LOG.error("Validator_pattern:{}".format(e.validator_value))
            LOG.error("Validator:{}".format(e.validator))
            sys.exit()
        except jsonschema.exceptions.SchemaError as e:
            LOG.error("Schema Validation Error!!")
            LOG.error("Message:{}".format(e.message))
            LOG.error("Schema:{}".format(e.schema))
            LOG.error("Validator_value:{}".format(e.validator_value))
            LOG.error("Validator:{}".format(e.validator))
            LOG.error("path:{}".format(e.path))
            sys.exit()

        LOG.info("Data validation Passed!")

    def _apply_design_rules(self):
        """ Applies design rules from rules.yaml


        These rules are used to determine ip address allocation ranges,
        host profile interfaces and also to create hardware profile
        information. The method calls corresponding rule hander function
        based on rule name and applies them to appropriate data objects.
        """
        LOG.info("Apply design rules")
        rules_dir = pkg_resources.resource_filename('spyglass', 'config/')
        rules_file = rules_dir + 'rules.yaml'
        rules_data_raw = self._read_file(rules_file)
        rules_yaml = yaml.safe_load(rules_data_raw)
        rules_data = {}
        rules_data.update(rules_yaml)

        for rule in rules_data.keys():
            rule_name = rules_data[rule]['name']
            function_str = "_apply_rule_" + rule_name
            rule_data_name = rules_data[rule][rule_name]
            function = getattr(self, function_str)
            function(rule_data_name)
            LOG.info("Applying rule:{}".format(rule_name))

    def _apply_rule_host_profile_interfaces(self, rule_data):
        pass

    def _apply_rule_hardware_profile(self, rule_data):
        pass

    def _apply_rule_ip_alloc_offset(self, rule_data):
        """ Offset allocation rules to determine ip address range(s)


        This rule is applied to incoming network data to determine
        network address, gateway ip and other address ranges
        """
        LOG.info("Apply network design rules")
        vlan_network_data = {}

        # Collect Rules
        default_ip_offset = rule_data['default']
        oob_ip_offset = rule_data['oob']
        gateway_ip_offset = rule_data['gateway']
        ingress_vip_offset = rule_data['ingress_vip']
        # static_ip_end_offset for non pxe network
        static_ip_end_offset = rule_data['static_ip_end']
        # dhcp_ip_end_offset for pxe network
        dhcp_ip_end_offset = rule_data['dhcp_ip_end']

        # Set ingress vip and CIDR for bgp
        LOG.info("Applying rule to network bgp data")
        subnet = netaddr.IPNetwork(
            self.data['network']['vlan_network_data']['ingress']['subnet'][0])
        ips = list(subnet)
        self.data['network']['bgp']['ingress_vip'] = str(
            ips[ingress_vip_offset])
        self.data['network']['bgp']['public_service_cidr'] = self.data[
            'network']['vlan_network_data']['ingress']['subnet'][0]
        LOG.debug("Updated network bgp data:\n{}".format(
            pprint.pformat(self.data['network']['bgp'])))

        LOG.info("Applying rule to vlan network data")
        # Get network subnets
        network_subnets = self._get_network_subnets()
        # Apply rules to vlan networks
        for net_type in network_subnets:
            if net_type == 'oob':
                ip_offset = oob_ip_offset
            else:
                ip_offset = default_ip_offset
            vlan_network_data[net_type] = {}
            subnet = network_subnets[net_type]
            ips = list(subnet)

            vlan_network_data[net_type]['network'] = str(
                network_subnets[net_type])

            vlan_network_data[net_type]['gateway'] = str(
                ips[gateway_ip_offset])

            vlan_network_data[net_type]['reserved_start'] = str(ips[1])
            vlan_network_data[net_type]['reserved_end'] = str(ips[ip_offset])

            static_start = str(ips[ip_offset + 1])
            static_end = str(ips[static_ip_end_offset])

            if net_type == 'pxe':
                mid = len(ips) // 2
                static_end = str(ips[mid - 1])
                dhcp_start = str(ips[mid])
                dhcp_end = str(ips[dhcp_ip_end_offset])

                vlan_network_data[net_type]['dhcp_start'] = dhcp_start
                vlan_network_data[net_type]['dhcp_end'] = dhcp_end

            vlan_network_data[net_type]['static_start'] = static_start
            vlan_network_data[net_type]['static_end'] = static_end

            # There is no vlan for oob network
            if (net_type != 'oob'):
                vlan_network_data[net_type]['vlan'] = self.data['network'][
                    'vlan_network_data'][net_type]['vlan']

            # OAM have default routes. Only for cruiser. TBD
            if (net_type == 'oam'):
                routes = ["0.0.0.0/0"]
            else:
                routes = []
            vlan_network_data[net_type]['routes'] = routes

            # Update network data to self.data
            self.data['network']['vlan_network_data'][
                net_type] = vlan_network_data[net_type]

        LOG.debug("Updated vlan network data:\n{}".format(
            pprint.pformat(vlan_network_data)))

    def load_extracted_data_from_data_source(self, extracted_data):
        """
        Function called from spyglass.py to pass extracted data
        from input data source
        """
        LOG.info("Load extracted data from data source")
        self._validate_extracted_data(extracted_data)
        self.data = extracted_data
        LOG.debug("Extracted data from plugin data source:\n{}".format(
            pprint.pformat(extracted_data)))
        extracted_file = "extracted_file.yaml"
        yaml_file = yaml.dump(extracted_data, default_flow_style=False)
        with open(extracted_file, 'w') as f:
            f.write(yaml_file)

        # Append region_data supplied from CLI to self.data
        self.data['region_name'] = self.region_name

    def dump_intermediary_file(self, intermediary_dir):
        """ Dumping intermediary yaml """
        LOG.info("Dumping intermediary yaml")
        intermediary_file = "{}_intermediary.yaml".format(
            self.data['region_name'])

        # Check of if output dir = intermediary_dir exists
        if intermediary_dir is not None:
            outfile = "{}/{}".format(intermediary_dir, intermediary_file)
        else:
            outfile = intermediary_file
        LOG.info("Intermediary file dir:{}".format(outfile))
        yaml_file = yaml.dump(self.data, default_flow_style=False)
        with open(outfile, 'w') as f:
            f.write(yaml_file)

    def generate_intermediary_yaml(self):
        """ Generating intermediary yaml """
        LOG.info("Generating intermediary yaml")
        self._apply_design_rules()
        self._get_genesis_node_details()
        self.intermediary_yaml = self.data
        return self.intermediary_yaml
