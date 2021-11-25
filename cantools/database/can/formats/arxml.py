# Load and dump a CAN database in ARXML format.

import re
import logging
from decimal import Decimal

from xml.etree import ElementTree

from ..signal import Signal, NamedSignalValue
from ..signal import Decimal as SignalDecimal
from ..message import Message
from ..internal_database import InternalDatabase

LOGGER = logging.getLogger(__name__)

class AutosarDatabaseSpecifics(object):
    """This class collects the AUTOSAR specific information of a system

    Message-specific AUTOSAR information is represented by the
    AutosarMessageSpecifics.

    """
    def __init__(self):
        pass

class AutosarMessageSpecifics(object):
    """This class collects all AUTOSAR specific information of a CAN message

    This means useful information about CAN messages which is provided
    by ARXML files, but is specific to AUTOSAR.
    """

    def __init__(self):
        pass


def parse_int_string(in_string):
    in_string = in_string.strip()
    if not in_string:
        return 0
    elif in_string[0] == '0' and in_string[1:2].isdigit():
        # interpret strings starting with a 0 as octal because
        # python's int(*, 0) does not for some reason.
        return int(in_string, 8)

    return int(in_string, 0) # autodetect the base

class SystemLoader(object):
    def __init__(self, root, strict):
        self._root = root
        self._strict = strict

        m = re.match('^\\{(.*)\\}AUTOSAR$', self._root.tag)

        if not m:
            raise ValueError(f"No XML namespace specified or illegal root tag "
                             f"name '{self._root.tag}'")

        xml_namespace = m.group(1)
        self.xml_namespace = xml_namespace
        self._xml_namespaces = { 'ns': xml_namespace }

        m = re.match('^http://autosar\.org/schema/r(4\.[0-9.]*)$',
                     xml_namespace)

        if m:
            # AUTOSAR 4: For some reason, all AR 4 revisions always
            # use "http://autosar.org/schema/r4.0" as their XML
            # namespace. To find out the exact revision used (i.e.,
            # 4.0, 4.1, 4.2, ...), the "xsi:schemaLocation" attribute
            # of the root tag needs to be examined. Since this is
            # pretty fragile (the used naming scheme has changed
            # during the AR4 journey and with the latest naming scheme
            # there seems to be no programmatic way to associate the
            # schemaLocation with the AR revision), we pretend to
            # always use AR 4.0...
            autosar_version_string = m.group(1)

        else:
            m = re.match('^http://autosar\.org/(3\.[0-9.]*)$', xml_namespace)

            if m:
                # AUTOSAR 3
                autosar_version_string = m.group(1)

            else:
                m = re.match('^http://autosar\.org/([0-9.]*)\.DAI\.[0-9]$',
                             xml_namespace)

                if m:
                    # Daimler (for some model ranges)
                    autosar_version_string = m.group(1)

                else:
                    raise ValueError(f"Unrecognized AUTOSAR XML namespace "
                                     f"'{xml_namespace}'")

        m = re.match('^([0-9]*)(\.[0-9]*)?(\.[0-9]*)?$', autosar_version_string)

        if not m:
            raise ValueError(f"Could not parse AUTOSAR version "
                             f"'{autosar_version_string}'")

        self.autosar_version_major = \
            int(m.group(1))
        self.autosar_version_minor = \
            0 if m.group(2) is None else int(m.group(2)[1:])
        self.autosar_version_patch = \
            0 if m.group(3) is None else int(m.group(3)[1:])

        if self.autosar_version_major != 4 and self.autosar_version_major != 3:
            raise ValueError('This class only supports AUTOSAR '
                             'versions 3 and 4')

        self._create_arxml_reference_dicts()

    def autosar_version_newer(self, major, minor=None, patch=None):
        """Returns true iff the AUTOSAR version specified in the ARXML it at
        least as the version specified by the function parameters

        If a part of the specified version is 'None', it and the
        'lesser' parts of the version are not considered. Also, the
        major version number *must* be specified.
        """

        if self.autosar_version_major > major:
            return True
        elif self.autosar_version_major < major:
            return False

        # the major part of the queried version is identical to the
        # one used by the ARXML
        if minor is None:
            # don't care
            return True
        elif self.autosar_version_minor > minor:
            return True
        elif self.autosar_version_minor < minor:
            return False

        # the major and minor parts of the queried version are identical
        # to the one used by the ARXML
        if patch is None:
            # don't care
            return True
        elif self.autosar_version_patch > patch:
            return True
        elif self.autosar_version_patch < patch:
            return False

        # all parts of the queried version are identical to the one
        # actually used by the ARXML
        return True

    def load(self):
        buses = []
        messages = []
        version = None
        autosar_specifics = AutosarDatabaseSpecifics()

        # recursively extract all CAN clusters of all AUTOSAR packages
        # in the XML tree
        def handle_package_list(package_list):

            # load all packages of an XML package list tag
            for package in package_list.iterfind('./ns:AR-PACKAGE',
                                                 self._xml_namespaces):
                # deal with the package contents
                self._load_package_contents(package, messages)

                # load all sub-packages
                if self.autosar_version_newer(4):
                    sub_package_list = package.find('./ns:AR-PACKAGES',
                                                self._xml_namespaces)

                else:
                    # AUTOSAR 3
                    sub_package_list = package.find('./ns:SUB-PACKAGES',
                                                    self._xml_namespaces)

                if sub_package_list is not None:
                    handle_package_list(sub_package_list)

        if self.autosar_version_newer(4):
            handle_package_list(self._root.find("./ns:AR-PACKAGES",
                                                self._xml_namespaces))
        else:
            # AUTOSAR3 puts the top level packages beneath the
            # TOP-LEVEL-PACKAGES XML tag.
            handle_package_list(self._root.find("./ns:TOP-LEVEL-PACKAGES",
                                                self._xml_namespaces))

        return InternalDatabase(messages,
                                [],
                                buses,
                                version,
                                autosar_specifics=autosar_specifics)

    def _load_package_contents(self, package_elem, messages):
        """This code extracts the information about CAN clusters of an
        individual AR package

        TODO: deal with the individual CAN buses
        """

        if self.autosar_version_newer(4):
            frame_triggerings_spec = \
                [
                    'ELEMENTS',
                    '*&CAN-CLUSTER',
                    'CAN-CLUSTER-VARIANTS',
                    '*&CAN-CLUSTER-CONDITIONAL',
                    'PHYSICAL-CHANNELS',
                    '*&CAN-PHYSICAL-CHANNEL',
                    'FRAME-TRIGGERINGS',
                    '*&CAN-FRAME-TRIGGERING'
                ]

        # AUTOSAR 3
        else:
            frame_triggerings_spec = \
                [
                    'ELEMENTS',
                    '*&CAN-CLUSTER',
                    'PHYSICAL-CHANNELS',
                    '*&PHYSICAL-CHANNEL',

                    # ATTENTION! The trailig 'S' here is in purpose:
                    # It appears in the AUTOSAR 3.2 XSD, but it still
                    # seems to be a typo in the spec...
                    'FRAME-TRIGGERINGSS',

                    '*&CAN-FRAME-TRIGGERING'
                ]

        can_frame_triggerings = \
            self._get_arxml_children(package_elem, frame_triggerings_spec)

        for can_frame_triggering in can_frame_triggerings:
            messages.append(self._load_message(can_frame_triggering))

    def _load_message(self, can_frame_triggering):
        """Load given message and return a message object.

        """

        # Default values.
        cycle_time = None
        senders = []
        autosar_specifics = AutosarMessageSpecifics()

        can_frame = self._get_can_frame(can_frame_triggering)

        # Name, frame id, length, is_extended_frame and comment.
        name = self._load_message_name(can_frame)
        frame_id = self._load_message_frame_id(can_frame_triggering)
        length = self._load_message_length(can_frame)
        is_extended_frame = \
            self._load_message_is_extended_frame(can_frame_triggering)
        comments = self._load_comments(can_frame)

        # ToDo: senders

        # For "sane" bus systems like CAN or LIN, there ought to be
        # only a single PDU per frame. AUTOSAR also supports "insane"
        # bus systems like flexray, though...
        pdu = self._get_pdu(can_frame)
        assert pdu is not None

        _, _, signals, cycle_time = \
            self._load_pdu(pdu, name, 1)

        return Message(frame_id=frame_id,
                       is_extended_frame=is_extended_frame,
                       name=name,
                       length=length,
                       senders=senders,
                       send_type=None,
                       cycle_time=cycle_time,
                       signals=signals,
                       comment=comments,
                       bus_name=None,
                       autosar_specifics=autosar_specifics,
                       strict=self._strict)


    def _load_pdu(self, pdu, frame_name, next_selector_idx):
        # Find all signals in this PDU.
        signals = []

        bit_length = self._get_unique_arxml_child(pdu, 'LENGTH')
        if bit_length is not None:
            bit_length = parse_int_string(bit_length.text)

        if self.autosar_version_newer(4):
            time_period_location = [
                'I-PDU-TIMING-SPECIFICATIONS',
                'I-PDU-TIMING',
                'TRANSMISSION-MODE-DECLARATION',
                'TRANSMISSION-MODE-TRUE-TIMING',
                'CYCLIC-TIMING',
                'TIME-PERIOD',
                'VALUE',
            ]
        else:
            time_period_location = [
                'I-PDU-TIMING-SPECIFICATION',
                'CYCLIC-TIMING',
                'REPEATING-TIME',
                'VALUE',
            ]

        time_period = \
            self._get_unique_arxml_child(pdu, time_period_location)

        cycle_time = None
        if time_period is not None:
            cycle_time = int(float(time_period.text) * 1000)

        # ordinary non-multiplexed message
        signals = self._load_pdu_signals(pdu)

        if pdu.tag == f'{{{self.xml_namespace}}}MULTIPLEXED-I-PDU':
            # multiplexed signals
            signals.extend(self._load_pdu_multiplexed_parts(pdu,
                                                            frame_name,
                                                            next_selector_idx))

        return \
            next_selector_idx, \
            bit_length, \
            signals, \
            cycle_time

    def _load_pdu_multiplexed_parts(self, pdu, frame_name, next_selector_idx):
        selector_pos = \
            self._get_unique_arxml_child(pdu, 'SELECTOR-FIELD-START-POSITION')
        selector_pos = parse_int_string(selector_pos.text)

        selector_len = \
            self._get_unique_arxml_child(pdu, 'SELECTOR-FIELD-LENGTH')
        selector_len = parse_int_string(selector_len.text)

        selector_byte_order = \
            self._get_unique_arxml_child(pdu, 'SELECTOR-FIELD-BYTE-ORDER')
        if selector_byte_order is not None:
            if selector_byte_order.text == 'MOST-SIGNIFICANT-BYTE-FIRST':
                selector_byte_order = 'big_endian'
            else:
                assert selector_byte_order.text == 'MOST-SIGNIFICANT-BYTE-LAST'
                selector_byte_order = 'little_endian'
        else:
            selector_byte_order = 'little_endian'

        selector_signal = Signal(
            name=f'{frame_name}_selector{next_selector_idx}',
            start=selector_pos,
            length=selector_len,
            byte_order=selector_byte_order,
            choices={},
            is_multiplexer=True,
        )
        next_selector_idx += 1

        signals = [ selector_signal ]

        if self.autosar_version_newer(4):
            dynpart_spec = [
                                                   'DYNAMIC-PARTS',
                                                   '*DYNAMIC-PART',
                                                   'DYNAMIC-PART-ALTERNATIVES',
                                                   '*DYNAMIC-PART-ALTERNATIVE',
                                               ]
        else:
            dynpart_spec = [
                                                   'DYNAMIC-PART',
                                                   'DYNAMIC-PART-ALTERNATIVES',
                                                   '*DYNAMIC-PART-ALTERNATIVE',
                                               ]

        for dynalt in self._get_arxml_children(pdu, dynpart_spec):
            dynalt_selector_value = \
                self._get_unique_arxml_child(dynalt, 'SELECTOR-FIELD-CODE')
            dynalt_selector_value = parse_int_string(dynalt_selector_value.text)
            dynalt_pdu = self._get_unique_arxml_child(dynalt, '&I-PDU')

            next_selector_idx, \
                dynalt_bit_length, \
                dynalt_signals, \
                dynalt_cycle_time \
                = self._load_pdu(dynalt_pdu, frame_name, next_selector_idx)

            is_initial = \
                self._get_unique_arxml_child(dynalt, 'INITIAL-DYNAMIC-PART')
            is_initial = \
                True \
                if is_initial is not None and is_initial.text == 'true' \
                else False
            if is_initial:
                assert selector_signal.initial is None
                selector_signal.initial = dynalt_selector_value

            # remove the selector signal from the dynamic part (because it
            # logically is in the static part, despite the fact that AUTOSAR
            # includes it in every dynamic part)
            dynalt_selector_signals = \
                [ x for x in dynalt_signals if x.start == selector_pos ]
            assert len(dynalt_selector_signals) == 1
            assert dynalt_selector_signals[0].start == selector_pos
            assert dynalt_selector_signals[0].length == selector_len
            if dynalt_selector_signals[0].choices is not None:
                selector_signal.choices.update(dynalt_selector_signals[0].choices)
            dynalt_signals.remove(dynalt_selector_signals[0])

            # copy the non-selector signals into the list of signals
            # for the PDU. TODO: It would be nicer if the hierarchic
            # structure of the message could be preserved, but this
            # would require a major change in the database format.
            for sig in dynalt_signals:
                # if a given signal is not already under the wings of
                # a sub-multiplexer signal, we claim it for ourselfs
                if sig.multiplexer_signal is None:
                    sig.multiplexer_signal = selector_signal.name
                    sig.multiplexer_ids = [ dynalt_selector_value ]

            signals.extend(dynalt_signals)

            # TODO: the cycle time of the multiplexers can be
            # specified indepently. how should this be handled?

        # the static part of the multiplexed PDU
        if self.autosar_version_newer(4):
            static_pdus_spec = [
                'STATIC-PARTS',
                '*STATIC-PART',
                '&I-PDU',
            ]
        else:
            static_pdus_spec = [
                'STATIC-PART',
                '&I-PDU',
            ]

        for static_pdu in self._get_arxml_children(pdu, static_pdus_spec):
            next_selector_idx, \
                bit_length, \
                static_signals, \
                _ \
                = self._load_pdu(static_pdu, frame_name, next_selector_idx)

            signals.extend(static_signals)

        return signals

    def _load_pdu_signals(self, pdu):
        signals = []

        if self.autosar_version_newer(4):
            # in AR4, "normal" PDUs use I-SIGNAL-TO-PDU-MAPPINGS whilst network
            # management PDUs use I-SIGNAL-TO-I-PDU-MAPPINGS
            i_signal_to_i_pdu_mappings = \
                self._get_arxml_children(pdu,
                                         [
                                             'I-SIGNAL-TO-PDU-MAPPINGS',
                                             '*&I-SIGNAL-TO-I-PDU-MAPPING'
                                         ])
            i_signal_to_i_pdu_mappings.extend(
                self._get_arxml_children(pdu,
                                         [
                                             'I-SIGNAL-TO-I-PDU-MAPPINGS',
                                             '*&I-SIGNAL-TO-I-PDU-MAPPING'
                                         ]))
        else:
            # in AR3, "normal" PDUs use SIGNAL-TO-PDU-MAPPINGS whilst network
            # management PDUs use I-SIGNAL-TO-I-PDU-MAPPINGS
            i_signal_to_i_pdu_mappings = \
                self._get_arxml_children(pdu,
                                         [
                                             'SIGNAL-TO-PDU-MAPPINGS',
                                             '*&I-SIGNAL-TO-I-PDU-MAPPING'
                                         ])

            i_signal_to_i_pdu_mappings.extend(
                self._get_arxml_children(pdu,
                                         [
                                             'I-SIGNAL-TO-I-PDU-MAPPINGS',
                                             '*&I-SIGNAL-TO-I-PDU-MAPPING'
                                         ]))

        for i_signal_to_i_pdu_mapping in i_signal_to_i_pdu_mappings:
            signal = self._load_signal(i_signal_to_i_pdu_mapping)

            if signal is not None:
                signals.append(signal)

        return signals

    def _load_message_name(self, can_frame_triggering):
        return self._get_unique_arxml_child(can_frame_triggering,
                                            'SHORT-NAME').text

    def _load_message_frame_id(self, can_frame_triggering):
        return parse_int_string(
            self._get_unique_arxml_child(can_frame_triggering,
                                         'IDENTIFIER').text)

    def _load_message_length(self, can_frame):
        return parse_int_string(
            self._get_unique_arxml_child(can_frame,
                                         'FRAME-LENGTH').text)

    def _load_message_is_extended_frame(self, can_frame_triggering):
        can_addressing_mode = \
            self._get_unique_arxml_child(can_frame_triggering,
                                         'CAN-ADDRESSING-MODE')

        return False if can_addressing_mode is None \
                     else can_addressing_mode.text == 'EXTENDED'

    def _load_comments(self, node):
        result = {}

        for l_2 in self._get_arxml_children(node, ['DESC', '*L-2']):
            lang = l_2.attrib.get('L', 'EN')
            result[lang] = l_2.text

        if len(result) == 0:
            return None

        return result

    def _load_signal(self, i_signal_to_i_pdu_mapping):
        """Load given signal and return a signal object.

        """
        i_signal = self._get_i_signal(i_signal_to_i_pdu_mapping)

        if i_signal is None:
            # No I-SIGNAL found, i.e. this i-signal-to-i-pdu-mapping is
            # probably a i-signal group. According to the XSD, I-SIGNAL and
            # I-SIGNAL-GROUP-REF are mutually exclusive...
            return None

        # Get the system signal XML node. This may also be a system signal
        # group, in which case we have ignore it if the XSD is to be believed.
        # ARXML is great!
        system_signal = self._get_unique_arxml_child(i_signal, '&SYSTEM-SIGNAL')

        if system_signal is not None \
           and system_signal.tag != f'{{{self.xml_namespace}}}SYSTEM-SIGNAL':
            return None

        # Default values.
        initial = None
        minimum = None
        maximum = None
        factor = 1
        offset = 0
        unit = None
        choices = None
        comments = None
        receivers = []
        decimal = SignalDecimal(Decimal(factor), Decimal(offset))

        if self.autosar_version_newer(4):
            i_signal_spec = '&I-SIGNAL'
        else:
            i_signal_spec = '&SIGNAL'

        i_signal = self._get_unique_arxml_child(i_signal_to_i_pdu_mapping,
                                                i_signal_spec)
        # Name, start position, length and byte order.
        name = self._load_signal_name(i_signal)
        start_position = \
            self._load_signal_start_position(i_signal_to_i_pdu_mapping)
        length = self._load_signal_length(i_signal, system_signal)
        byte_order = self._load_signal_byte_order(i_signal_to_i_pdu_mapping)

        # Type.
        is_signed, is_float = self._load_signal_type(i_signal)

        if system_signal is not None:
            # Minimum, maximum, factor, offset and choices.
            minimum, maximum, factor, offset, choices, unit, comments = \
                self._load_system_signal(system_signal, decimal, is_float)

        # loading initial values is way too complicated, so it is the
        # job of a separate method
        initial = self._load_arxml_init_value_string(i_signal, system_signal)

        if initial is not None:
            initial_int = None
            try:
                initial_int = parse_int_string(initial)
            except:
                pass

            if choices is not None and initial_int in choices:
                initial = choices[initial_int]
            elif is_float:
                initial = float(initial)*factor + offset
            elif initial.strip().lower() == 'true':
                initial = True
            elif initial.strip().lower() == 'false':
                initial = False
            # TODO: strings?
            else:
                initial = initial_int*factor + offset

        # ToDo: receivers

        return Signal(name=name,
                      start=start_position,
                      length=length,
                      receivers=receivers,
                      byte_order=byte_order,
                      is_signed=is_signed,
                      scale=factor,
                      offset=offset,
                      initial=initial,
                      minimum=minimum,
                      maximum=maximum,
                      unit=unit,
                      choices=choices,
                      comment=comments,
                      is_float=is_float,
                      decimal=decimal)

    def _load_signal_name(self, i_signal):
        return self._get_unique_arxml_child(i_signal,
                                            'SHORT-NAME').text

    def _load_signal_start_position(self, i_signal_to_i_pdu_mapping):
        pos = self._get_unique_arxml_child(i_signal_to_i_pdu_mapping,
                                           'START-POSITION').text
        return parse_int_string(pos)

    def _load_signal_length(self, i_signal, system_signal):
        i_signal_length = self._get_unique_arxml_child(i_signal, 'LENGTH')

        if i_signal_length is not None:
            return parse_int_string(i_signal_length.text)

        if not self.autosar_version_newer(4) and system_signal is not None:
            # AUTOSAR3 supports specifying the signal length via the
            # system signal. (AR4 does not.)
            system_signal_length = \
                self._get_unique_arxml_child(system_signal, 'LENGTH')

            if system_signal_length is not None:
                # get the length from the system signal.
                return parse_int_string(system_signal_length.text)

        return None # error?!

    def _load_arxml_init_value_string(self, i_signal, system_signal):
        """"Load the initial value of a signal

        Supported mechanisms are references to constants and direct
        specifcation of the value. Note that this method returns a
        string which must be converted into the signal's data type by
        the calling code.
        """

        # AUTOSAR3 specifies the signal's initial value via
        # the system signal via the i-signal...
        if self.autosar_version_newer(4):
            if i_signal is None:
                return None

            return self._load_arxml_init_value_string_helper(i_signal)
        else:
            if system_signal is None:
                return None

            return self._load_arxml_init_value_string_helper(system_signal)

    def _load_arxml_init_value_string_helper(self, signal_elem):
        """"Helper function for loading thge initial value of a signal

        This function avoids code duplication between loading the
        initial signal value from the ISignal and the
        SystemSignal. (The latter is only supported by AUTOSAR 3.)
        """
        if self.autosar_version_newer(4):
            value_elem = \
                self._get_unique_arxml_child(signal_elem,
                                             [
                                                'INIT-VALUE',
                                                'NUMERICAL-VALUE-SPECIFICATION',
                                                'VALUE'
                                             ])

            if value_elem is not None:
                # initial value is specified directly.
                return value_elem.text

            value_elem = \
                self._get_unique_arxml_child(signal_elem,
                                             [
                                                'INIT-VALUE',
                                                'CONSTANT-REFERENCE',
                                                '&CONSTANT',
                                                'VALUE-SPEC',
                                                'NUMERICAL-VALUE-SPECIFICATION',
                                                'VALUE'
                                             ])

            if value_elem is not None:
                # initial value is specified via a reference to a constant.
                return value_elem.text

            # no initial value specified or specified in a way which we
            # don't recognize
            return None

        else:
            # AUTOSAR3: AR3 seems to specify initial values by means
            # of INIT-VALUE-REF elements. Unfortunately, these are not
            # standard references so we have to go down a separate
            # code path...
            ref_elem = signal_elem.find(f'./ns:INIT-VALUE-REF',
                                        self._xml_namespaces)

            if ref_elem is None:
                # no initial value found here
                return None

            literal_spec = \
                self._follow_arxml_reference(
                    base_elem=signal_elem,
                    arxml_path=ref_elem.text,
                    dest_tag_name=ref_elem.attrib.get('DEST'),
                    refbase_name=ref_elem.attrib.get('BASE'))
            if literal_spec is None:
                # dangling reference...
                return None

            literal_value = \
                literal_spec.find(f'./ns:VALUE', self._xml_namespaces)
            return None if literal_value is None else literal_value.text

    def _load_signal_byte_order(self, i_signal_to_i_pdu_mapping):
        packing_byte_order = \
            self._get_unique_arxml_child(i_signal_to_i_pdu_mapping,
                                         'PACKING-BYTE-ORDER')

        if packing_byte_order is not None \
           and packing_byte_order.text == 'MOST-SIGNIFICANT-BYTE-FIRST':
            return 'big_endian'
        else:
            return 'little_endian'

    def _load_system_signal_unit(self, system_signal, compu_method):
        res = self._get_unique_arxml_child(system_signal,
                                           [
                                               'PHYSICAL-PROPS',
                                               'SW-DATA-DEF-PROPS-VARIANTS',
                                               '&SW-DATA-DEF-PROPS-CONDITIONAL',
                                               '&UNIT',
                                               'DISPLAY-NAME'
                                           ])

        if res is None and compu_method is not None:
            # try to go via the compu_method
            res = self._get_unique_arxml_child(compu_method,
                                               [
                                                   '&UNIT',
                                                   'DISPLAY-NAME'
                                               ])

        ignorelist = ( "NoUnit", )

        if res is None or res.text in ignorelist:
            return None
        return res.text

    def _load_texttable(self, compu_method, decimal, is_float):
        minimum = None
        maximum = None
        choices = {}

        text_to_num_fn = float if is_float else parse_int_string

        for compu_scale in self._get_arxml_children(compu_method,
                                                    [
                                                      '&COMPU-INTERNAL-TO-PHYS',
                                                      'COMPU-SCALES',
                                                      '*&COMPU-SCALE'
                                                    ]):
            lower_limit = \
                self._get_unique_arxml_child(compu_scale, 'LOWER-LIMIT')
            upper_limit = \
                self._get_unique_arxml_child(compu_scale, 'UPPER-LIMIT')
            vt = \
               self._get_unique_arxml_child(compu_scale, ['&COMPU-CONST', 'VT'])
            comments = self._load_comments(compu_scale)

            # range of the internal values of the scale.
            minimum_int_scale = \
               None if lower_limit is None else text_to_num_fn(lower_limit.text)
            maximum_int_scale = \
               None if upper_limit is None else text_to_num_fn(upper_limit.text)

            # for texttables the internal and the physical values are identical
            if minimum is None:
                minimum = minimum_int_scale
            elif minimum_int_scale is not None:
                minimum = min(minimum, minimum_int_scale)

            if maximum is None:
                maximum = maximum_int_scale
            elif maximum_int_scale is not None:
                maximum = max(maximum, maximum_int_scale)

            if vt is not None:
                value = parse_int_string(lower_limit.text)
                name = vt.text
                choices[value] = NamedSignalValue(value, name, comments)

        decimal.minimum = minimum
        decimal.maximum = maximum
        return minimum, maximum, choices

    def _load_linear_factor_and_offset(self, compu_scale, decimal):
        compu_rational_coeffs = \
            self._get_unique_arxml_child(compu_scale, '&COMPU-RATIONAL-COEFFS')

        if compu_rational_coeffs is None:
            return None, None

        numerators = self._get_arxml_children(compu_rational_coeffs,
                                              ['&COMPU-NUMERATOR', '*&V'])

        if len(numerators) != 2:
            raise ValueError(
                'Expected 2 numerator values for linear scaling, but '
                'got {}.'.format(len(numerators)))

        denominators = self._get_arxml_children(compu_rational_coeffs,
                                                ['&COMPU-DENOMINATOR', '*&V'])

        if len(denominators) != 1:
            raise ValueError(
                'Expected 1 denominator value for linear scaling, but '
                'got {}.'.format(len(denominators)))

        denominator = Decimal(denominators[0].text)
        decimal.scale = Decimal(numerators[1].text) / denominator
        decimal.offset = Decimal(numerators[0].text) / denominator

        return float(decimal.scale), float(decimal.offset)

    def _load_linear(self, compu_method, decimal, is_float):
        compu_scale = self._get_unique_arxml_child(compu_method,
                                                   [
                                                       'COMPU-INTERNAL-TO-PHYS',
                                                       'COMPU-SCALES',
                                                       '&COMPU-SCALE'
                                                   ])

        lower_limit = self._get_unique_arxml_child(compu_scale, '&LOWER-LIMIT')
        upper_limit = self._get_unique_arxml_child(compu_scale, '&UPPER-LIMIT')

        # range of the internal values
        minimum_int = \
            None if lower_limit is None else parse_int_string(lower_limit.text)
        maximum_int = \
            None if upper_limit is None else parse_int_string(upper_limit.text)

        factor, offset = \
            self._load_linear_factor_and_offset(compu_scale, decimal)

        factor = 1.0 if factor is None else factor
        offset = 0.0 if offset is None else offset

        # range of the physical values
        minimum = None if minimum_int is None else minimum_int*factor + offset
        maximum = None if maximum_int is None else maximum_int*factor + offset
        decimal.minimum = None if minimum is None else Decimal(minimum)
        decimal.maximum = None if maximum is None else Decimal(maximum)

        return minimum, maximum, factor, offset

    def _load_scale_linear_and_texttable(self, compu_method, decimal, is_float):
        minimum = None
        maximum = None
        factor = 1
        offset = 0
        choices = {}

        for compu_scale in self._get_arxml_children(compu_method,
                                                    [
                                                      '&COMPU-INTERNAL-TO-PHYS',
                                                      'COMPU-SCALES',
                                                      '*&COMPU-SCALE'
                                                    ]):

            lower_limit = \
                self._get_unique_arxml_child(compu_scale, 'LOWER-LIMIT')
            upper_limit = \
                self._get_unique_arxml_child(compu_scale, 'UPPER-LIMIT')
            vt = \
               self._get_unique_arxml_child(compu_scale, ['&COMPU-CONST', 'VT'])
            comments = self._load_comments(compu_scale)

            # range of the internal values of the scale.
            minimum_int_scale = \
                None if lower_limit is None \
                else parse_int_string(lower_limit.text)
            maximum_int_scale = \
               None if upper_limit is None \
               else parse_int_string(upper_limit.text)

            # TODO: make sure that no conflicting scaling factors and offsets
            # are specified. For now, let's just assume that the ARXML file is
            # well formed.
            factor_scale, offset_scale = \
                self._load_linear_factor_and_offset(compu_scale, decimal)
            if factor_scale is not None:
                factor = factor_scale
            else:
                factor_scale = 1.0

            if offset_scale is not None:
                offset = offset_scale
            else:
                offset_scale = 0.0

            # range of the physical values of the scale.
            if minimum is None:
                minimum = minimum_int_scale*factor_scale + offset_scale
            elif minimum_int_scale is not None:
                minimum = min(minimum,
                              minimum_int_scale*factor_scale + offset_scale)

            if maximum is None:
                maximum = maximum_int_scale*factor_scale + offset_scale
            elif maximum_int_scale is not None:
                maximum = max(maximum,
                              maximum_int_scale*factor_scale + offset_scale)

            if vt is not None:
                assert(minimum_int_scale is not None \
                       and minimum_int_scale == maximum_int_scale)
                value = minimum_int_scale
                name = vt.text
                choices[value] = NamedSignalValue(value, name, comments)

        decimal.minimum = Decimal(minimum)
        decimal.maximum = Decimal(maximum)
        return minimum, maximum, factor, offset, choices

    def _load_system_signal(self, system_signal, decimal, is_float):
        minimum = None
        maximum = None
        factor = 1
        offset = 0
        choices = None

        compu_method = self._get_compu_method(system_signal)

        # Unit and comment.
        unit = self._load_system_signal_unit(system_signal, compu_method)
        comments = self._load_comments(system_signal)

        if compu_method is not None:
            category = self._get_unique_arxml_child(compu_method, 'CATEGORY')

            if category is None:
                # if no category is specified, we assume that the
                # physical value of the signal corresponds to its
                # binary representation.
                return (minimum,
                        maximum,
                        factor,
                        offset,
                        choices,
                        unit,
                        comments)

            category = category.text

            if category == 'TEXTTABLE':
                minimum, maximum, choices = \
                    self._load_texttable(compu_method, decimal,  is_float)
            elif category == 'LINEAR':
                minimum, maximum, factor, offset = \
                    self._load_linear(compu_method, decimal,  is_float)
            elif category == 'SCALE_LINEAR_AND_TEXTTABLE':
                (minimum,
                 maximum,
                 factor,
                 offset,
                 choices) = self._load_scale_linear_and_texttable(compu_method,
                                                                  decimal,
                                                                  is_float)
            else:
                LOGGER.debug('Compu method category %s is not yet implemented.',
                             category)

        return \
            minimum, \
            maximum, \
            1 if factor is None else factor, \
            0 if offset is None else offset, \
            choices, \
            unit, \
            comments

    def _load_signal_type(self, i_signal):
        is_signed = False
        is_float = False

        base_type = self._get_sw_base_type(i_signal)

        if base_type is not None:
            base_type_encoding = \
                self._get_unique_arxml_child(base_type, '&BASE-TYPE-ENCODING')

            if base_type_encoding is None:
                btt = base_type.find('./ns:SHORT-NAME', self._xml_namespaces)
                btt = bt.text
                raise ValueError(
                    f'BASE-TYPE-ENCODING in base type "{btt}" does not exist.')

            base_type_encoding = base_type_encoding.text

            if base_type_encoding in ('2C', '1C', 'SM'):
                # types which use two-complement, one-complement or
                # sign+magnitude encodings are signed. TODO (?): The
                # fact that if anything other than two complement
                # notation is used for negative numbers is not
                # reflected anywhere. In practice this should not
                # matter, though, since two-complement notation is
                # basically always used for systems build after
                # ~1970...
                is_signed = True
            elif base_type_encoding == 'IEEE754':
                is_float = True

        return is_signed, is_float

    def _follow_arxml_reference(self,
                                base_elem,
                                arxml_path,
                                dest_tag_name=None,
                                refbase_name=None):
        """Resolve an ARXML reference

        It returns the ElementTree node which corresponds to the given
        path through the ARXML package structure. If no such node
        exists, a None object is returned.
        """

        # Handle relative references by converting them into absolute
        # ones
        if not arxml_path.startswith("/"):
            base_path = self._node_to_arxml_path[base_elem].split("/")

            # Find the absolute path specified by the applicable
            # reference base. The spec says the matching reference
            # base for the "closest" package should be used, so we
            # traverse the ARXML path of the base element in reverse
            # to find the first package with a matching reference
            # base.
            refbase_path = None
            for i in range(len(base_path), 0, -1):
                test_path = '/'.join(base_path[0:i])
                test_node = self._arxml_path_to_node.get(test_path)
                if test_node is not None \
                   and test_node.tag  != f'{{{self.xml_namespace}}}AR-PACKAGE':
                    # the referenced XML node does not represent a
                    # package
                    continue

                if refbase_name is None:
                    # the caller did not specify a BASE attribute,
                    # i.e., we ought to use the closest default
                    # reference base
                    refbase_path = \
                        self._package_default_refbase_path.get(test_path)
                    if refbase_path is None:
                        # bad luck: this package does not specify a
                        # default reference base
                        continue
                    else:
                        break

                # the caller specifies a BASE attribute
                refbase_path = \
                    self._package_refbase_paths.get(test_path, {}) \
                                               .get(refbase_name)
                if refbase_path is None:
                    # bad luck: this package does not specify a
                    # reference base with the specified name
                    continue
                else:
                    break

            if refbase_path is None:
                raise ValueError(f"Unknown reference base '{refbase_name}' "
                                 f"for relative ARXML reference '{arxml_path}'")

            arxml_path = f'{refbase_path}/{arxml_path}'

        # resolve the absolute reference: This is simple because we
        # have a path -> XML node dictionary!
        result = self._arxml_path_to_node.get(arxml_path)

        if result is not None \
           and dest_tag_name is not None \
           and result.tag != f'{{{self.xml_namespace}}}{dest_tag_name}':
            # the reference could be resolved but it lead to a node of
            # unexpected kind
            return None

        return result


    def _create_arxml_reference_dicts(self):
        self._node_to_arxml_path = {}
        self._arxml_path_to_node = {}
        self._package_default_refbase_path = {}
        # given a package name, produce a refbase label to ARXML path dictionary
        self._package_refbase_paths = {}

        def add_sub_references(elem, elem_path, cur_package_path=""):
            """Recursively add all ARXML references contained within an XML
            element to the dictionaries to handle ARXML references"""

            # check if a short name has been attached to the current
            # element. If yes update the ARXML path for this element
            # and its children
            short_name = elem.find(f'ns:SHORT-NAME', self._xml_namespaces)

            if short_name is not None:
                short_name = short_name.text
                elem_path = f'{elem_path}/{short_name}'

                if elem_path in self._arxml_path_to_node:
                    raise ValueError(f"File contains multiple elements with "
                                     f"path '{elem_path}'")

                self._arxml_path_to_node[elem_path] = elem

            # register the ARXML path name of the current element
            self._node_to_arxml_path[elem] = elem_path

            # if the current element is a package, update the ARXML
            # package path
            if elem.tag == f'{{{self.xml_namespace}}}AR-PACKAGE':
                cur_package_path = f'{cur_package_path}/{short_name}'

            # handle reference bases (for relative references)
            if elem.tag == f'{{{self.xml_namespace}}}REFERENCE-BASE':
                refbase_name = elem.find('./ns:SHORT-LABEL',
                                         self._xml_namespaces).text.strip()
                refbase_path = elem.find('./ns:PACKAGE-REF',
                                         self._xml_namespaces).text.strip()

                is_default = elem.find('./ns:IS-DEFAULT', self._xml_namespaces)

                if is_default is not None:
                    is_default = (is_default.text.strip().lower() == "true")

                current_default_refbase_path = \
                    self._package_default_refbase_path.get(cur_package_path)

                if is_default and current_default_refbase_path is not None:
                    raise ValueError(f'Multiple default reference bases bases '
                                     f'specified for package '
                                     f'"{cur_package_path}".')
                elif is_default:
                    self._package_default_refbase_path[cur_package_path] = \
                        refbase_path

                is_global = elem.find('./ns:IS-GLOBAL', self._xml_namespaces)

                if is_global is not None:
                    is_global = (is_global.text.strip().lower() == "true")

                if is_global:
                    raise ValueError(f'Non-canonical relative references are '
                                     f'not yet supported.')

                # ensure that a dictionary for the refbases of the package exists
                if cur_package_path not in self._package_refbase_paths:
                    self._package_refbase_paths[cur_package_path] = {}
                elif refbase_name in \
                     self._package_refbase_paths[cur_package_path]:
                    raise ValueError(f'Package "{cur_package_path}" specifies '
                                     f'multiple reference bases named '
                                     f'"{refbase_name}".')
                self._package_refbase_paths[cur_package_path][refbase_name] = \
                    refbase_path

            # iterate over all children and add all references contained therein
            for child in elem:
                add_sub_references(child, elem_path, cur_package_path)

        self._arxml_path_to_node = {}
        add_sub_references(self._root, '')

    def _get_arxml_children(self, base_elems, children_location):
        """Locate a set of ElementTree child nodes at a given location.

        This is a method that retrieves a list of ElementTree nodes
        that match a given ARXML location. An ARXML location is a list
        of strings that specify the nesting order of the XML tag
        names; potential references for entries are preceeded by an
        '&': If a sub-element exhibits the specified name, it is used
        directly and if there is a sub-node called
        '{child_tag_name}-REF', it is assumed to contain an ARXML
        reference. This reference is then resolved and the remaining
        location specification is relative to the result of that
        resolution. If a location atom is preceeded by '*', then
        multiple sub-elements are possible. The '&' and '*' qualifiers
        may be combined.

        Example:

        .. code:: text

          # Return all frame triggerings in any physical channel of a
          # CAN cluster, where each conditional, each the physical
          # channel and its individual frame triggerings can be
          # references
          loader._get_arxml_children(can_cluster,
                                     [
                                         'CAN-CLUSTER-VARIANTS',
                                         '*&CAN-CLUSTER-CONDITIONAL',
                                         'PHYSICAL-CHANNELS',
                                         '*&CAN-PHYSICAL-CHANNEL',
                                         'FRAME-TRIGGERINGS',
                                         '*&CAN-FRAME-TRIGGERING'
                                     ])

        """

        if base_elems is None:
            raise ValueError(
                'Cannot retrieve a child element of a non-existing node!')

        # make sure that the children_location is a list. for convenience we
        # also allow it to be a string. In this case we take it that a
        # direct child node needs to be found.
        if isinstance(children_location, str):
            children_location = [ children_location ]

        # make sure that the base elements are iterable. for
        # convenience we also allow it to be an individiual node.
        if type(base_elems).__name__ == 'Element':
            base_elems = [base_elems]

        for child_tag_name in children_location:

            if len(base_elems) == 0:
                return [] # the base elements left are the empty set...

            # handle the set and reference specifiers of the current
            # sub-location
            allow_references = '&' in child_tag_name[:2]
            is_nodeset = '*' in child_tag_name[:2]

            if allow_references:
                child_tag_name = child_tag_name[1:]

            if is_nodeset:
                child_tag_name = child_tag_name[1:]

            # traverse the specified path one level deeper
            result = []

            for base_elem in base_elems:
                local_result = []

                for child_elem in base_elem:
                    ctt = f'{{{self.xml_namespace}}}{child_tag_name}'
                    cttr = f'{{{self.xml_namespace}}}{child_tag_name}-REF'

                    if child_elem.tag == ctt:
                        local_result.append(child_elem)
                    elif child_elem.tag == cttr:
                        tmp = self._follow_arxml_reference(
                            base_elem=base_elem,
                            arxml_path=child_elem.text,
                            dest_tag_name=child_elem.attrib.get('DEST'),
                            refbase_name=child_elem.attrib.get('BASE'))

                        if tmp is None:
                            raise ValueError(f'Encountered dangling reference '
                                             f'{child_tag_name}-REF: '
                                             f'{child_elem.text}')

                        local_result.append(tmp)

                if not is_nodeset and len(local_result) > 1:
                    raise ValueError(f'Encountered a a non-unique child node '
                                     f'of type {child_tag_name} which ought to '
                                     f'be unique')

                result.extend(local_result)

            base_elems = result

        return base_elems

    def _get_unique_arxml_child(self, base_elem, child_location):
        """This method does the same as get_arxml_children, but it assumes
        that the location yields at most a single node.

        It returns None if no match was found and it raises ValueError
        if multiple nodes match the location, i.e., the returned
        object can be used directly if the corresponding node is
        assumed to be present.
        """
        tmp = self._get_arxml_children(base_elem, child_location)

        if len(tmp) == 0:
            return None
        elif len(tmp) == 1:
            return tmp[0]
        else:
            raise ValueError(f'{child_location} does not resolve into a '
                             f'unique node')

    def _get_can_frame(self, can_frame_triggering):
        return self._get_unique_arxml_child(can_frame_triggering, '&FRAME')

    def _get_i_signal(self, i_signal_to_i_pdu_mapping):
        if self.autosar_version_newer(4):
            return self._get_unique_arxml_child(i_signal_to_i_pdu_mapping,
                                                '&I-SIGNAL')
        else:
            return self._get_unique_arxml_child(i_signal_to_i_pdu_mapping,
                                                '&SIGNAL')

    def _get_pdu(self, can_frame):
        return self._get_unique_arxml_child(can_frame,
                                            [
                                                'PDU-TO-FRAME-MAPPINGS',
                                                '&PDU-TO-FRAME-MAPPING',
                                                '&PDU'
                                            ])

    def _get_compu_method(self, system_signal):
        if self.autosar_version_newer(4):
            return self._get_unique_arxml_child(system_signal,
                                                [
                                               '&PHYSICAL-PROPS',
                                               'SW-DATA-DEF-PROPS-VARIANTS',
                                               '&SW-DATA-DEF-PROPS-CONDITIONAL',
                                               '&COMPU-METHOD'
                                                ])
        else:
            return self._get_unique_arxml_child(system_signal,
                                                [
                                                    '&DATA-TYPE',
                                                    'SW-DATA-DEF-PROPS',
                                                    '&COMPU-METHOD'
                                                ])

    def _get_sw_base_type(self, i_signal):
        return self._get_unique_arxml_child(i_signal,
                                            [
                                               '&NETWORK-REPRESENTATION-PROPS',
                                               'SW-DATA-DEF-PROPS-VARIANTS',
                                               '&SW-DATA-DEF-PROPS-CONDITIONAL',
                                               '&BASE-TYPE'
                                            ])

# The ARXML XML namespace for the EcuExtractLoader
NAMESPACE = 'http://autosar.org/schema/r4.0'
NAMESPACES = {'ns': NAMESPACE}

ROOT_TAG = '{{{}}}AUTOSAR'.format(NAMESPACE)

# ARXML XPATHs used by the EcuExtractLoader
def make_xpath(location):
    return './ns:' + '/ns:'.join(location)

ECUC_VALUE_COLLECTION_XPATH = make_xpath([
    'AR-PACKAGES',
    'AR-PACKAGE',
    'ELEMENTS',
    'ECUC-VALUE-COLLECTION'
])
ECUC_MODULE_CONFIGURATION_VALUES_REF_XPATH = make_xpath([
    'ECUC-VALUES',
    'ECUC-MODULE-CONFIGURATION-VALUES-REF-CONDITIONAL',
    'ECUC-MODULE-CONFIGURATION-VALUES-REF'
])
ECUC_REFERENCE_VALUE_XPATH = make_xpath([
    'REFERENCE-VALUES',
    'ECUC-REFERENCE-VALUE'
])
DEFINITION_REF_XPATH = make_xpath(['DEFINITION-REF'])
VALUE_XPATH = make_xpath(['VALUE'])
VALUE_REF_XPATH = make_xpath(['VALUE-REF'])
SHORT_NAME_XPATH = make_xpath(['SHORT-NAME'])
PARAMETER_VALUES_XPATH = make_xpath(['PARAMETER-VALUES'])
REFERENCE_VALUES_XPATH = make_xpath([
    'REFERENCE-VALUES'
])

class EcuExtractLoader(object):

    def __init__(self, root, strict):
        self.root = root
        self.strict = strict

    def load(self):
        buses = []
        messages = []
        version = None

        ecuc_value_collection = self.root.find(ECUC_VALUE_COLLECTION_XPATH,
                                               NAMESPACES)
        values_refs = ecuc_value_collection.iterfind(
            ECUC_MODULE_CONFIGURATION_VALUES_REF_XPATH,
            NAMESPACES)
        com_xpaths = [
            value_ref.text
            for value_ref in values_refs
            if value_ref.text.endswith('/Com')
        ]

        if len(com_xpaths) != 1:
            raise ValueError(
                'Expected 1 /Com, but got {}.'.format(len(com_xpaths)))

        com_config = self.find_com_config(com_xpaths[0] + '/ComConfig')

        for ecuc_container_value in com_config:
            definition_ref = ecuc_container_value.find(DEFINITION_REF_XPATH,
                                                       NAMESPACES).text

            if not definition_ref.endswith('ComIPdu'):
                continue

            message = self.load_message(ecuc_container_value)

            if message is not None:
                messages.append(message)

        return InternalDatabase(messages,
                                [],
                                buses,
                                version)

    def load_message(self, com_i_pdu):
        # Default values.
        interval = None
        senders = []
        comments = None

        # Name, frame id, length and is_extended_frame.
        name = com_i_pdu.find(SHORT_NAME_XPATH, NAMESPACES).text
        direction = None

        for parameter, value in self.iter_parameter_values(com_i_pdu):
            if parameter == 'ComIPduDirection':
                direction = value
                break

        com_pdu_id_ref = None

        for reference, value in self.iter_reference_values(com_i_pdu):
            if reference == 'ComPduIdRef':
                com_pdu_id_ref = value
                break

        if com_pdu_id_ref is None:
            raise ValueError('No ComPduIdRef reference found.')

        if direction == 'SEND':
            frame_id, length, is_extended_frame = self.load_message_tx(
                com_pdu_id_ref)
        elif direction == 'RECEIVE':
            frame_id, length, is_extended_frame = self.load_message_rx(
                com_pdu_id_ref)
        else:
            raise NotImplementedError(
                'Direction {} not supported.'.format(direction))

        if frame_id is None:
            LOGGER.warning('No frame id found for message %s.', name)

            return None

        if is_extended_frame is None:
            LOGGER.warning('No frame type found for message %s.', name)

            return None

        if length is None:
            LOGGER.warning('No length found for message %s.', name)

            return None

        # ToDo: interval, senders, comments

        # Find all signals in this message.
        signals = []
        values = com_i_pdu.iterfind(ECUC_REFERENCE_VALUE_XPATH,
                                    NAMESPACES)

        for value in values:
            definition_ref = value.find(DEFINITION_REF_XPATH,
                                        NAMESPACES).text
            if not definition_ref.endswith('ComIPduSignalRef'):
                continue

            value_ref = value.find(VALUE_REF_XPATH, NAMESPACES)
            signal = self.load_signal(value_ref.text)

            if signal is not None:
                signals.append(signal)

        return Message(frame_id=frame_id,
                       is_extended_frame=is_extended_frame,
                       name=name,
                       length=length,
                       senders=senders,
                       send_type=None,
                       cycle_time=interval,
                       signals=signals,
                       comment=comments,
                       bus_name=None,
                       strict=self.strict)

    def load_message_tx(self, com_pdu_id_ref):
        return self.load_message_rx_tx(com_pdu_id_ref,
                                       'CanIfTxPduCanId',
                                       'CanIfTxPduDlc',
                                       'CanIfTxPduCanIdType')

    def load_message_rx(self, com_pdu_id_ref):
        return self.load_message_rx_tx(com_pdu_id_ref,
                                       'CanIfRxPduCanId',
                                       'CanIfRxPduDlc',
                                       'CanIfRxPduCanIdType')

    def load_message_rx_tx(self,
                           com_pdu_id_ref,
                           parameter_can_id,
                           parameter_dlc,
                           parameter_can_id_type):
        can_if_tx_pdu_cfg = self.find_can_if_rx_tx_pdu_cfg(com_pdu_id_ref)
        frame_id = None
        length = None
        is_extended_frame = None

        if can_if_tx_pdu_cfg is not None:
            for parameter, value in self.iter_parameter_values(can_if_tx_pdu_cfg):
                if parameter == parameter_can_id:
                    frame_id = int(value)
                elif parameter == parameter_dlc:
                    length = int(value)
                elif parameter == parameter_can_id_type:
                    is_extended_frame = (value == 'EXTENDED_CAN')

        return frame_id, length, is_extended_frame

    def load_signal(self, xpath):
        ecuc_container_value = self.find_value(xpath)
        if ecuc_container_value is None:
            return None

        name = ecuc_container_value.find(SHORT_NAME_XPATH, NAMESPACES).text

        # Default values.
        is_signed = False
        is_float = False
        minimum = None
        maximum = None
        factor = 1
        offset = 0
        unit = None
        choices = None
        comments = None
        receivers = []
        decimal = SignalDecimal(Decimal(factor), Decimal(offset))

        # Bit position, length, byte order, is_signed and is_float.
        bit_position = None
        length = None
        byte_order = None

        for parameter, value in self.iter_parameter_values(ecuc_container_value):
            if parameter == 'ComBitPosition':
                bit_position = int(value)
            elif parameter == 'ComBitSize':
                length = int(value)
            elif parameter == 'ComSignalEndianness':
                byte_order = value.lower()
            elif parameter == 'ComSignalType':
                if value in ['SINT8', 'SINT16', 'SINT32']:
                    is_signed = True
                elif value in ['FLOAT32', 'FLOAT64']:
                    is_float = True

        if bit_position is None:
            LOGGER.warning('No bit position found for signal %s.',name)

            return None

        if length is None:
            LOGGER.warning('No bit size found for signal %s.', name)

            return None

        if byte_order is None:
            LOGGER.warning('No endianness found for signal %s.', name)

            return None

        # ToDo: minimum, maximum, factor, offset, unit, choices,
        #       comments and receivers.

        return Signal(name=name,
                      start=bit_position,
                      length=length,
                      receivers=receivers,
                      byte_order=byte_order,
                      is_signed=is_signed,
                      scale=factor,
                      offset=offset,
                      minimum=minimum,
                      maximum=maximum,
                      unit=unit,
                      choices=choices,
                      comment=comments,
                      is_float=is_float,
                      decimal=decimal)

    def find_com_config(self, xpath):
        return self.root.find(make_xpath([
            "AR-PACKAGES",
            "AR-PACKAGE/[ns:SHORT-NAME='{}']".format(xpath.split('/')[1]),
            "ELEMENTS",
            "ECUC-MODULE-CONFIGURATION-VALUES/[ns:SHORT-NAME='Com']",
            "CONTAINERS",
            "ECUC-CONTAINER-VALUE/[ns:SHORT-NAME='ComConfig']",
            "SUB-CONTAINERS"
        ]),
                              NAMESPACES)

    def find_value(self, xpath):
        return self.root.find(make_xpath([
            "AR-PACKAGES",
            "AR-PACKAGE/[ns:SHORT-NAME='{}']".format(xpath.split('/')[1]),
            "ELEMENTS",
            "ECUC-MODULE-CONFIGURATION-VALUES/[ns:SHORT-NAME='Com']",
            "CONTAINERS",
            "ECUC-CONTAINER-VALUE/[ns:SHORT-NAME='ComConfig']",
            "SUB-CONTAINERS",
            "ECUC-CONTAINER-VALUE/[ns:SHORT-NAME='{}']".format(xpath.split('/')[-1])
        ]),
                              NAMESPACES)

    def find_can_if_rx_tx_pdu_cfg(self, com_pdu_id_ref):
        messages = self.root.iterfind(
            make_xpath([
                "AR-PACKAGES",
                "AR-PACKAGE/[ns:SHORT-NAME='{}']".format(
                    com_pdu_id_ref.split('/')[1]),
                "ELEMENTS",
                "ECUC-MODULE-CONFIGURATION-VALUES/[ns:SHORT-NAME='CanIf']",
                'CONTAINERS',
                "ECUC-CONTAINER-VALUE/[ns:SHORT-NAME='CanIfInitCfg']",
                'SUB-CONTAINERS',
                'ECUC-CONTAINER-VALUE'
            ]),
            NAMESPACES)

        for message in messages:
            definition_ref = message.find(DEFINITION_REF_XPATH,
                                          NAMESPACES).text

            if definition_ref.endswith('CanIfTxPduCfg'):
                expected_reference = 'CanIfTxPduRef'
            elif definition_ref.endswith('CanIfRxPduCfg'):
                expected_reference = 'CanIfRxPduRef'
            else:
                continue

            for reference, value in self.iter_reference_values(message):
                if reference == expected_reference:
                    if value == com_pdu_id_ref:
                        return message

    def iter_parameter_values(self, param_conf_container):
        parameters = param_conf_container.find(PARAMETER_VALUES_XPATH,
                                               NAMESPACES)

        if parameters is None:
            raise ValueError('PARAMETER-VALUES does not exist.')

        for parameter in parameters:
            definition_ref = parameter.find(DEFINITION_REF_XPATH,
                                            NAMESPACES).text
            value = parameter.find(VALUE_XPATH, NAMESPACES).text
            name = definition_ref.split('/')[-1]

            yield name, value

    def iter_reference_values(self, param_conf_container):
        references = param_conf_container.find(REFERENCE_VALUES_XPATH,
                                               NAMESPACES)

        if references is None:
            raise ValueError('REFERENCE-VALUES does not exist.')

        for reference in references:
            definition_ref = reference.find(DEFINITION_REF_XPATH,
                                            NAMESPACES).text
            value = reference.find(VALUE_REF_XPATH, NAMESPACES).text
            name = definition_ref.split('/')[-1]

            yield name, value

def is_ecu_extract(root):
    ecuc_value_collection = root.find(ECUC_VALUE_COLLECTION_XPATH,
                                      NAMESPACES)

    return ecuc_value_collection is not None

def load_string(string, strict=True):
    """Parse given ARXML format string.

    """

    root = ElementTree.fromstring(string)

    m = re.match("{(.*)}AUTOSAR", root.tag)
    if not m:
        raise ValueError(f"No XML namespace specified or illegal root tag name '{root.tag}'")
    xml_namespace = m.group(1)

    # Should be replaced with a validation using the XSD file.
    recognized_namespace = False
    if re.match("http://autosar.org/schema/r(4.*)", xml_namespace) \
       or re.match("http://autosar.org/(3.*)", xml_namespace) \
       or re.match("http://autosar.org/(.*)\.DAI\.[0-9]", xml_namespace):
        recognized_namespace = True

    if not recognized_namespace:
        raise ValueError(f"Unrecognized XML namespace '{xml_namespace}'")

    if is_ecu_extract(root):
        if root.tag != ROOT_TAG:
            raise ValueError(
                'Expected root element tag {}, but got {}.'.format(
                    ROOT_TAG,
                    root.tag))

        return EcuExtractLoader(root, strict).load()
    else:
        return SystemLoader(root, strict).load()
