import sys
from urllib3.exceptions import NewConnectionError

from influxdb import InfluxDBClient
from speedflux.logs import log
from requests.exceptions import ConnectionError


class Influx:
    def __init__(self, config):
        self.config = config
        self._client = None
        self.retries = 0
        self.init_db()

    @property
    def client(self):
        if not self._client:
            self._client = InfluxDBClient(
                self.config['db_host'],
                self.config['db_port'],
                self.config['db_user'],
                self.config['db_pass'],
                None)
            log.debug("Client extablished")
        return self._client

    def init_db(self):

        try:
            log.debug("Intitializing Influx Database")
            databases = self.client.get_list_database()
            if len(list(filter(
                    lambda x: x['name'] == self.config['db_name'], databases))
                   ) == 0:
                self.client.create_database(
                    self.config['db_name'])  # Create if does not exist.
            else:
                # Switch to if does exist.
                self.client.switch_database(self.config['db_name'])
            self.initilized = True
        except (ConnectionError, NewConnectionError) as bad_host:
            if self.retries == 3:
                log.error("Database Init failed for 3rd time. Exiting")
                sys.exit()
            self.retries += 1
            log.error("Connection to influx host was refused. This likely "
                      "means that the DB is down or INFLUX_DB_ADDRESS is "
                      f"incorrect. It's currently '{self.config['db_host']}'")
            log.error("Full Error follows\n")
            log.error(bad_host)
            log.error(f"Retry {self.retries}: Initiliazing DB.")
            self.init_db()

    def format_data(self, data):
        influx_data = [
            {
                'measurement': 'ping',
                'time': data['timestamp'],
                'fields': {
                    'jitter': data['ping'].get('jitter', 0),
                    'latency': data['ping'].get('latency', 0)
                }
            },
            {
                'measurement': 'download',
                'time': data['timestamp'],
                'fields': {
                    # Byte to Megabit
                    'bandwidth': data['download'].get('bandwidth', 0) / 125000,
                    'bytes': data['download'].get('bytes', 0),
                    'elapsed': data['download']['elapsed']
                }
            },
            {
                'measurement': 'upload',
                'time': data['timestamp'],
                'fields': {
                    # Byte to Megabit
                    'bandwidth': data['upload'].get('bandwidth', 0) / 125000,
                    'bytes': data['upload']['bytes'],
                    'elapsed': data['upload']['elapsed']
                }
            },
            {
                'measurement': 'packetLoss',
                'time': data['timestamp'],
                'fields': {
                    'packetLoss': int(data.get('packetLoss', 0))
                }
            },
            {
                'measurement': 'speeds',
                'time': data['timestamp'],
                'fields': {
                    'jitter': data['ping'].get('jitter', 0),
                    'latency': data['ping'].get('latency', 0),
                    'packetLoss': int(data.get('packetLoss', 0)),
                    # Byte to Megabit
                    'bandwidth_down': data['download'].get(
                        'bandwidth', 0) / 125000,
                    'bytes_down': data['download'].get(
                        'bytes', 0),
                    'elapsed_down': data['download'].get(
                        'elapsed'),
                    # Byte to Megabit
                    'bandwidth_up': data['upload'].get(
                        'bandwidth', 0) / 125000,
                    'bytes_up': data['upload'].get(
                        'bytes', 0),
                    'elapsed_up': data['upload'].get(
                        'elapsed')
                }
            }
        ]
        tags = self.tag_selection(data)
        if tags is not None:
            for measurement in influx_data:
                measurement['tags'] = tags

        return influx_data

    def write(self, data, data_type='Speedtest'):
        try:
            if self.client.write_points(data):
                log.info(F"{data_type} data written successfully")
                log.debug(F"Wrote `{data}` to Influx")
                self.retries = 0
            else:
                raise Exception(F"{data_type} write points did not complete")
        except (ConnectionError, NewConnectionError, Exception) as \
                bad_connection:
            if self.retries == 3:
                log.error('Max retries exceeded for write. Check that data '
                          'base is on and can receive data')
                log.error('Exiting')
                sys.exit()

            log.error("Connection error occurred during write")
            log.error(bad_connection)
            self.retries += 1
            log.error("Reinitiating database and retrying.")
            self.init_db()
            self.write(data, data_type)

        except Exception as err:
            log.error(F"{err}")

    def tag_selection(self, data):
        tags = self.config['db_tags']
        options = {}

        # tag_switch takes in _data and attaches CLIoutput to more readable ids
        tag_switch = {
            'namespace': self.config['namespace'],
            'isp': data['isp'],
            'interface': data['interface']['name'],
            'internal_ip': data['interface']['internalIp'],
            'interface_mac': data['interface']['macAddr'],
            'vpn_enabled': (
                False if data['interface']['isVpn'] == 'false' else True),
            'external_ip': data['interface']['externalIp'],
            'server_id': data['server']['id'],
            'server_name': data['server']['name'],
            'server_location': data['server']['location'],
            'server_country': data['server']['country'],
            'server_host': data['server']['host'],
            'server_port': data['server']['port'],
            'server_ip': data['server']['ip'],
            'speedtest_id': data['result']['id'],
            'speedtest_url': data['result']['url']
        }

        if tags is None:
            tags = 'namespace'
        elif '*' in tags:
            return tag_switch
        else:
            tags = 'namespace, ' + tags

        tags = tags.split(',')
        for tag in tags:
            # split the tag string, strip and add selected tags to {options}
            # with corresponding tag_switch data
            tag = tag.strip()
            options[tag] = tag_switch[tag]
        return options

    def process_data(self, data):
        data = self.format_data(data)
        self.write(data)
