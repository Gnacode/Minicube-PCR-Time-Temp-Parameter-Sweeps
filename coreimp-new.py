import threading
import time
import sys
import string
import zeroconf
import re
from six.moves import input
from zeroconf import ServiceBrowser, Zeroconf
import time
import requests
import socket
import csv
import uuid
import json
import jsonpickle
from IPython.utils.capture import capture_output
import ipywidgets as widgets

cubes = []
#Enter fixed output dir for CSV files
global MCP_Data_Dir
MCP_Data_Dir = ''

class EliasDelta(object):
    def __init__(self):
        self.bitreader = 0

    def decode(self, enc_data, offset):
        dec_data = []
        dec_data.append(offset)
        breader = BitReader(enc_data)
        while not breader.is_empty():
            num = 1
            len = 1
            length_of_len = 0
            # Calculate the number of bits used for length
            while not breader.read_bit():
                length_of_len = length_of_len + 1
            # Read the len
            for i in range(0, length_of_len):
                len = len << 1
                if breader.read_bit():
                    len = len | 0x1
            # Read in the data
            for i in range(0, len - 1):
                num = num << 1
                if breader.read_bit():
                    num = num | 0x1
            # Unwrap the negative numbers
            if num == 1:
                real_val = 0
            else:
                if num % 2:
                    real_val = (num - 1) / 2
                else:
                    real_val = -num / 2

            offset = offset + real_val
            dec_data.append(offset)

        return dec_data


class BitReader(object):
    def __init__(self, data):
        self.data = []
        self.bit_index = 0
        self.read_index = 0
        i = 0
        while i < len(data):
            hex_str = data[i:i + 2]
            self.data.append(int(hex_str, 16))
            i = i + 2

    def is_empty(self):
        if self.read_index == (len(self.data) - 1):
            val = self.data[self.read_index]
            mask = (0x01 << (7 - self.bit_index + 1)) - 1
            if val & mask:
                return False
        elif (self.read_index < len(self.data)) and self.bit_index < 8:
            return False

        return True

    def read_bit(self):
        bit_val = False
        if self.read_index < len(self.data):
            val = self.data[self.read_index]
            if (val >> (7 - self.bit_index)) & 0x01:
                bit_val = True
            self.bit_index = self.bit_index + 1
            if self.bit_index > 7:
                self.bit_index = 0
                self.read_index = self.read_index + 1
        else:
            print("Bit reader exception")

        return bit_val


# This object contains the decoded log data from the device
class LogData(object):
    def __init__(self):
        # Create empty arrays for holding the temperature, log number and stage ID's
        self.temperature = []
        self.log_number = []
        self.stageid = []
        # Create an Elias Delta decoder object
        self.ed = EliasDelta()

    def append(self, data):
        # Decode the log numbers
        lognum = self.ed.decode(data['encLogNumber'], int(data['lognumber']))
        self.log_number.extend(lognum)
        # Decode the temperatures
        temp = self.ed.decode(data['encTemp'], int(data['temp']))
        self.temperature.extend(temp)
        # Decode the stage ID's
        stage = self.ed.decode(data['encStageId'], int(data['stage']))
        self.stageid.extend(stage)
        # Return the number of entries currently in the log
        return len(self.stageid)


# This object contains the decoded log data from the device
class LogDataPWM(object):
    def __init__(self):
        # Create empty arrays for holding the temperature, log number and stage ID's
        self.pwm = []
        self.log_number = []
        self.stageid = []
        # Create an Elias Delta decoder object
        self.ed = EliasDelta()

    def append(self, pwmdata):
        # Decode the log numbers
        lognum = self.ed.decode(pwmdata['encpwmNumber'], int(pwmdata['pwmnumber']))
        self.log_number.extend(lognum)
        # Decode the temperatures
        temp = self.ed.decode(pwmdata['encpwm'], int(pwmdata['pwm']))
        self.pwm.extend(temp)
        # Decode the stage ID's
        stage = self.ed.decode(pwmdata['encStageId'], int(pwmdata['stage']))
        self.stageid.extend(stage)
        # Return the number of entries currently in the log
        return len(self.stageid)


class LogEntry(object):
    def __init__(self, tube_id, log_id):
        self.tubeid = tube_id
        self.logid = log_id
        self.index = 0
        self.data = LogData()
        self.pwmdata = LogDataPWM()


class Minicube(object):
    def __init__(self, address, port):
        self.ip_address = socket.inet_ntoa(address)
        self.port = port
        self.base_uri = 'http://{}:{}/api/control'.format(self.ip_address, self.port)
        self.lookup = {}
        self.experiments = []
        self.tubes = {}

    def fetch_experiments(self):
        uri_string = '{}/experiments?index={}&size={}'.format(self.base_uri, 0, 10000)
        print('Fetching experiments from %s...' % (uri_string,))
        resp = requests.request('GET', url=uri_string)
        jresp = resp.json()
        for exid in jresp['experiments']:
            experiment = MinicubeExperiment(exid, self)
            self.experiments.append(experiment)
            self.lookup[exid] = (len(self.experiments) - 1)

            # experiment = MinicubeExperiment("2338FF88-37E2-4FC9-B2C2-A3815D95A3B2", self)
            # self.experiments.append(experiment)

    def fetch_tubes(self):
        uri_string = '{}/tubestatus'.format(self.base_uri)
        print('Fetching tubes status from %s...' % (uri_string,))
        resp = requests.request('GET', url=uri_string)
        json_resp = resp.json()
        for device_tube in json_resp['tubestatus']:
            tube_id = int(device_tube['tubeid'])
            tube = MinicubeTube(tube_id, self, meta=device_tube['status'])
            self.tubes[tube_id] = tube
        print('Fetched {} tubes', len(self.tubes))

    def get_tube(self, tube_id):
        return self.tubes.get(tube_id, None)


class MinicubeExperiment(object):
    def __init__(self, id, cube):
        self.current_log = 0
        self.pwmcurrent_log = 0
        self.logs = []
        self.pwmlogs = []
        self.id = id
        self.cube = cube

    def fetch_details(self):
        uri_string = '{}/experiment?experimentid={}'.format(self.cube.base_uri, self.id)
        resp = requests.request('GET', url=uri_string)
        for log in resp.json()['experiment']:
            entry = LogEntry(log['tubeid'], log['logid'])
            self.logs.append(entry)
            self.pwmlogs.append(entry)

    def get_next_entry(self):
        log = self.logs[self.current_log]
        uri_string = '{}/log?logid={}&index={}&size={}'.format(self.cube.base_uri, log.logid, log.index, 10000)
        # print('Fetching log from %s' % (uri_string,))
        resp = requests.request('GET', url=uri_string)
        try:
            log_data = resp.json()['logdata']
        except:
            print('JSON decode error')
        else:
            if len(log_data) > 0:
                for log_line in log_data:
                    log.index = log.data.append(log_line)

                return True

        return False

    def get_next_pwm_entry(self):
        pwmlog = self.pwmlogs[self.pwmcurrent_log]
        uri_string = '{}/pwm?pwmid={}&index={}&size={}'.format(self.cube.base_uri, pwmlog.logid, pwmlog.index, 10000)
        # print('Fetching log from %s' % (uri_string,))
        resp = requests.request('GET', url=uri_string)
        print(uri_string)
        try:
            log_data = resp.json()['pwmdata']
        except:
            print('JSON decode error')
        else:
            if len(log_data) > 0:
                for log_line in log_data:
                    pwmlog.index = pwmlog.pwmdata.append(log_line)

                return True
        return False

    def fetch_log_data(self):
        print('Fetching log data for log entry: {}').format(self.current_log)
        while self.get_next_entry():
            sys.stdout.write('.')
            sys.stdout.flush()
        sys.stdout.write('\n')
        sys.stdout.flush()
        self.current_log = self.current_log + 1
        if self.current_log >= len(self.logs):
            return False
        else:
            return True

    def fetch_pwm_log_data(self):
        print('Fetching log data for log entry: {}'.format(self.pwmcurrent_log))
        print("Fetch PWM Log Data")
        while self.get_next_pwm_entry():
            sys.stdout.write('.')
            sys.stdout.flush()
        sys.stdout.write('\n')
        sys.stdout.flush()
        self.pwmcurrent_log = self.pwmcurrent_log + 1
        if self.pwmcurrent_log >= len(self.pwmlogs):
            return False
        else:
            return True


class MinicubeProtocols(object):
    def __init__(self, cube):
        self.cube = cube

    def fetch_protocols(self, from_index=None, to_index=None):
        pass


class MinicubeProtocol(object):
    def __init__(self, id, cube, profile=None):
        self.api_path = "/protocol"
        self.id = id
        self.cube = cube
        self.profile = profile

    def create(self):
        uri_string = '{}{}?protocolid={}'.format(self.cube.base_uri, self.api_path, self.id)
        json_data = MinicubeProtocolDTO(protocol=self).as_json()
        response = requests.post(uri_string, data=json_data)
        json_resp = response.json()
        print("Protocol with id {} successfully created".format(self.id))

    def delete(self):
        pass

    def get(self):
        pass


class MinicubeTubes(object):
    def __init__(self, cube):
        self.cube = cube
        self.tubestatus = None

    def status(self):
        uri_string = '{}/tubestatus'.format(self.cube.base_uri)
        print('Fetching tube status')
        response = requests.get(uri_string)
        json_resp = response.json()
        self.tubestatus = []
        for tube_obj in json_resp['tubestatus']:
            tube_resp_dto = MinicubeTubeStatusDTO(tube_obj['tubeid'], tube_obj['status'])
            self.tubestatus.append(tube_resp_dto)

    def get_tube(self, tubeid):
        if self.tubestatus is None:
            raise ValueError('Fetch tube status first')
        for tube in self.tubestatus:
            if tube.tubeid == tubeid:
                return tube
        raise ValueError('Tube with specified id does not exist')

    def get_tube_ids(self):
        return [tube.tubeid for tube in self.tubestatus]


class MinicubeTube(object):
    def __init__(self, id, cube, protocol=None, experiment=None, meta=None):
        self.id = id
        self.protocol = protocol
        self.experiment = experiment
        self.meta = meta
        self.cube = cube
        self.logs = {}

    def set_protocol(self, protocol):
        if not isinstance(protocol, MinicubeProtocol):
            raise TypeError("{} protocol must be of type MinicubeProtocol", protocol)
        self.protocol = protocol

    def set_experiment(self, experiment):
        if not isinstance(experiment, MinicubeExperiment):
            raise TypeError("{} experiment must be of type MinicubeExperiment".format(experiment))
        self.experiment = experiment

    def commit(self):
        uri_string = '{}/tube?protocolid={}&experimentid={}&tubeid={}&meta={}'.format(
            self.cube.base_uri,
            self.protocol.id,
            self.experiment.id,
            self.id,
            self.meta)
        response = requests.put(uri_string)
        json_resp = response.json()
        logid = json_resp['logid']
        self.logs[logid] = []
        print("Tube commited. Storing logid {}".format(logid))

    def uncommit(self):
        uri_string = '{}/tube?tubeid={}'.format(self.cube.base_uri, self.id)
        response = requests.delete(uri_string)
        MinicubeRequestHandler(response).extract_data()
        print("Uncommited from tube")


class JsonSerializable:
    def as_json(self):
        return jsonpickle.encode(self, unpicklable=False)


class MinicubeProtocolDTO(JsonSerializable):
    def __init__(self, **kwargs):
        protocol = kwargs.get('protocol', None)
        if protocol is not None:
            self.profile = protocol.profile
        self.profile = kwargs.get('profile', self.profile if hasattr(self, 'profile') else None)


class ProfileDTO(JsonSerializable):
    def __init__(self, cycles=None):
        if cycles is None:
            self.cycles = {}
        else:
            self.cycles = cycles


class CycleDTO(JsonSerializable):
    def __init__(self, components=None):
        if components is None:
            self.components = {}
        else:
            self.components = components


class ComponentDTO(JsonSerializable):
    def __init__(self, order, temperature, duration):
        self.order = order
        self.temperature = temperature
        self.duration = duration


class MinicubeTubeStatusDTO(object):
    def __init__(self, tubeid, status):
        self.tubeid = tubeid
        self.status = status


class MinicubeTubeResponseDTO(object):
    def __init__(self, json_string):
        json_dict = json.loads(json_string)
        for key, value in json_dict.items:
            self.__dict__[key] = value

    def __init__(self, tubes):
        self.tubestatus = tubes

        
class MinicubeUtils:
    @staticmethod
    def create_profile():
        stage1 = ComponentDTO(1, 55, 5)
        stage2 = ComponentDTO(2, 95, 5)
        cycle = CycleDTO([stage1, stage2])
        all_cycles = []
        for i in range(0, 20):
            all_cycles.append(cycle)
            
        return ProfileDTO(all_cycles);
        #return ProfileDTO(CycleDTO([ComponentDTO(1, 96, 5)])) # this is example of Profile - 1 stage, with 1 cycle, duration 5 s, 96C temp
    # stage1 = ComponentDTO(1, 96, 5)
    # stage2 = ComponentDTO(1, 90, 20)
    # stage3 = ComponentDTO(1, 94, 10)
    # cycle1 = CycleDTO([stage1]) - single stage
    # cycle2 = CycleDTO([stage2, stage3]) - two stages
    # cycles = [cycle1, cycle2] - two cycles
    # ProfileDTO(cycles) - entire profile

class MinicubeUtilsAnnealing:
    @staticmethod
    def create_profile_twostage(inimelttime, inimelttemp, melttime, melttemp, anntime, anntemp, num_of_cycles):
        stage0 = ComponentDTO(1, inimelttemp, inimelttime)
        stage1 = ComponentDTO(1, melttemp, melttime)
        stage2 = ComponentDTO(2, anntemp, anntime)
        all_cycles = []
        all_stages = []
        activation_cycle = CycleDTO([stage0])
        all_cycles.append(activation_cycle)
        for i in range(0, 20):
            all_stages.append(stage1)
            all_stages.append(stage2)
        print all_stages
        cycle = CycleDTO(all_stages)
        all_cycles.append(cycle)            
        return ProfileDTO(all_cycles);    

    @staticmethod
    def create_profile_threestage(inimelttime, inimelttemp, melttime, melttemp, anntime, anntemp, exttime, exttemp, num_of_cycles):
        stage0 = ComponentDTO(1, inimelttemp, inimelttemp)
        stage1 = ComponentDTO(1, melttemp, melttime)
        stage2 = ComponentDTO(2, anntemp, anntime)
        stage3 = ComponentDTO(3, exttemp, exttime)
        all_cycles = []
        all_stages = []
        activation_cycle = CycleDTO([stage0])
        all_cycles.append(activation_cycle)
        for i in range(0, num_of_cycles):
            all_stages.append(stage1)
            all_stages.append(stage2)
            all_stages.append(stage3)
        print all_stages
        cycle = CycleDTO(all_stages)
        all_cycles.append(cycle)            
        return ProfileDTO(all_cycles);

class MinicubeListener:
    def remove_service(self, zeroc, type, name):
        print('Service %s removed' % (name,))

    def add_service(self, zeroc, type, name):
        info = zeroc.get_service_info(type, name)
        print('%s' % (name))
        cube = Minicube(info.address, info.port)
        cubes.append(cube)
        
class MinicubeScanner:
    @staticmethod
    def scan_network():
        zeroconf = Zeroconf()
        listener = MinicubeListener()
        with capture_output() as minicubes_on_network:
            browser = ServiceBrowser(zeroconf, '_gnacode-pcr._tcp.local.', listener)
            time.sleep(10)
        browser.cancel()
        rx = re.compile("(?:minicubepcr-|^)[^.]*")
        strmc = str(minicubes_on_network.stdout) 
        mcselected = rx.findall(strmc)
        return mcselected

class MinicubeSweeps:
    @staticmethod
    def twostage_pcr_tempsweep(mcp, inimelttime, inimelttemp,initempstep, melttime, melttemp, melttempstep, anntime, anntemp, anntempstep, tubestart, tubeend, num_of_cycles):       
        mcp_device = mcp[0] + '.local.'
        print('Selected device {}').format(mcp_device)
        cube = Minicube(socket.inet_aton(socket.gethostbyname(mcp_device)), 80)
        tubes, experiments = [], []

        for exp_id in range(tubestart, tubeend+1):
            protocol = MinicubeProtocol(str(uuid.uuid4()), cube, MinicubeUtilsAnnealing.create_profile_twostage(inimelttime, inimelttemp+(exp_id-1)*initempstep, melttime, melttemp+(exp_id-1)*melttempstep, anntime, anntemp+(exp_id-1)*anntempstep, num_of_cycles))
            protocol.create()
            #protocol = MinicubeProtocol(str(uuid.uuid4()) , cube, MinicubeUtilsAnnealing.create_profile(starttemp+(exp_id-1)*tempstep)
            #protocol.create()
            experiments.append(MinicubeExperiment(str(uuid.uuid4()), cube))
            tubes.append(MinicubeTube(0 + exp_id, cube, meta='commited'))
            tubes[exp_id - 1].set_protocol(protocol)
            tubes[exp_id - 1].set_experiment(experiments[exp_id - 1])
            tubes[exp_id - 1].commit()
        return cube

    @staticmethod
    def threestage_pcr_tempsweep(mcp, inimelttime, inimelttemp, initempstep, melttime, melttemp, melttempstep, anntime, anntemp, anntempstep, exttime, exttemp, exttempstep, tubestart, tubeend, num_of_cycles):
        mcp_device = mcp[0] + '.local.'
        print('Selected device {}').format(mcp_device)
        cube = Minicube(socket.inet_aton(socket.gethostbyname(mcp_device)), 80)
        tubes, experiments = [], []

        for exp_id in range(tubestart, tubeend+1):
            protocol = MinicubeProtocol(str(uuid.uuid4()), cube, MinicubeUtilsAnnealing.create_profile_threestage(inimelttime, inimelttemp+(exp_id-1)*initempstep, melttime, melttemp+(exp_id-1)*melttempstep, anntime, anntemp+(exp_id-1)*anntempstep, exttime, exttemp+(exp_id-1)*exttempstep, num_of_cycles))
            protocol.create()
            experiments.append(MinicubeExperiment(str(uuid.uuid4()), cube))
            tubes.append(MinicubeTube(0 + exp_id, cube, meta='commited'))
            tubes[exp_id - 1].set_protocol(protocol)
            tubes[exp_id - 1].set_experiment(experiments[exp_id - 1])
            tubes[exp_id - 1].commit()
        return cube
        
    @staticmethod    
    def twostage_pcr_timesweep(mcp, inimelttime, inimelttemp,initimestep, melttime, melttemp, melttimestep, anntime, anntemp, anntimestep, tubestart, tubeend, num_of_cycles):       
        mcp_device = mcp[0] + '.local.'
        print('Selected device {}').format(mcp_device)
        cube = Minicube(socket.inet_aton(socket.gethostbyname(mcp_device)), 80)
        tubes, experiments = [], []

        for exp_id in range(tubestart, tubeend+1):
            protocol = MinicubeProtocol(str(uuid.uuid4()), cube, MinicubeUtilsAnnealing.create_profile_twostage(inimelttime+(exp_id-1)*initimestep, inimelttemp, melttime+(exp_id-1)*melttimestep, melttemp, anntime+(exp_id-1)*anntimestep, anntemp, num_of_cycles))
            protocol.create()
            #protocol = MinicubeProtocol(str(uuid.uuid4()) , cube, MinicubeUtilsAnnealing.create_profile(starttemp+(exp_id-1)*tempstep)
            #protocol.create()
            experiments.append(MinicubeExperiment(str(uuid.uuid4()), cube))
            tubes.append(MinicubeTube(0 + exp_id, cube, meta='commited'))
            tubes[exp_id - 1].set_protocol(protocol)
            tubes[exp_id - 1].set_experiment(experiments[exp_id - 1])
            tubes[exp_id - 1].commit()
        return cube

    @staticmethod
    def threestage_pcr_timesweep(mcp, inimelttime, inimelttemp, initimestep, melttime, melttemp, melttimestep, anntime, anntemp, anntimestep, exttime, exttemp, exttimestep, tubestart, tubeend, num_of_cycles):
        mcp_device = mcp[0] + '.local.'
        print('Selected device {}').format(mcp_device)
        cube = Minicube(socket.inet_aton(socket.gethostbyname(mcp_device)), 80)
        tubes, experiments = [], []

        for exp_id in range(tubestart, tubeend+1):
            protocol = MinicubeProtocol(str(uuid.uuid4()), cube, MinicubeUtilsAnnealing.create_profile_threestage(inimelttime+(exp_id-1)*initimestep, inimelttemp, melttime+(exp_id-1)*melttimestep, melttemp, anntime+(exp_id-1)*anntimestep, anntemp, exttime+(exp_id-1)*exttimestep, exttemp, num_of_cycles))
            protocol.create()
            experiments.append(MinicubeExperiment(str(uuid.uuid4()), cube))
            tubes.append(MinicubeTube(0 + exp_id, cube, meta='commited'))
            tubes[exp_id - 1].set_protocol(protocol)
            tubes[exp_id - 1].set_experiment(experiments[exp_id - 1])
            tubes[exp_id - 1].commit()
        return cube
print("All set")