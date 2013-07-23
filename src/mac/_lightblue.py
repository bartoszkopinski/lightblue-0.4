# Copyright (c) 2009 Bea Lam. All rights reserved.
#
# This file is part of LightBlue.
#
# LightBlue is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# LightBlue is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with LightBlue.  If not, see <http://www.gnu.org/licenses/>.

# Mac OS X main module implementation.

import types
import warnings

import Foundation
import AppKit
import objc

import _IOBluetooth
import _LightAquaBlue
import _lightbluecommon
import _macutil
import _bluetoothsockets


# public attributes
__all__ = ("finddevices", "findservices", "finddevicename", 
           "selectdevice", "selectservice",
           "gethostaddr", "gethostclass",
           "socket", 
           "advertise", "stopadvertise",
           "pair", "unpair")

# details of advertised services
__advertised = {}

def pair(addr):
    """
    pair(addr) -> (pairing_code, error)
    
    Initiates a pairing procedure with a device based its 
    device address, e.g. '00:18:33:5B:34:24'.

    Returns a tuple (pairing code, potential error code or None).
    """
    unpair(addr)
    try:
        p = _AsyncPair.alloc().initWithAddress_(addr)
        return p.start()
    except:
        return (0, -1)

def unpair(addr):
    device = _IOBluetooth.IOBluetoothDevice.withAddressString_(addr)
    if (device):
        device.remove() # Note: -remove is a private method...

def finddevices(getnames=True, timeout=10):
    inquiry = _SyncDeviceInquiry()
    inquiry.run(getnames, timeout)
    devices = inquiry.getfounddevices()
    return devices


def findservices(addr=None, name=None, servicetype=None):
    if servicetype not in (_lightbluecommon.RFCOMM, _lightbluecommon.OBEX, None):
        raise ValueError("servicetype must be RFCOMM, OBEX or None, was %s" % \
            servicetype)

    if addr is None:
        try:
            founddevices = finddevices()
        except _lightbluecommon.BluetoothError, e:
            msg = "findservices() failed, " +\
                    "error while finding devices: " + str(e)
            raise _lightbluecommon.BluetoothError(msg)
            
        #print founddevices
        addresses = [dev[0] for dev in founddevices]
    else:
        addresses = [addr]

    services = []
    for devaddr in addresses:
        iobtdevice = _IOBluetooth.IOBluetoothDevice.withAddressString_(devaddr)
            
        try:
            lastseen = iobtdevice.getLastServicesUpdate()
            if lastseen is None or lastseen.timeIntervalSinceNow() < -2:
                # perform SDP query to update known services.
                # wait at least a few seconds between service discovery cos 
                # sometimes it doesn't work if doing updates too often.
                # In future should have option to not do updates.
                serviceupdater = _SDPQueryRunner.alloc().init()
                try:
                    serviceupdater.query(iobtdevice)  # blocks until updated
                except _lightbluecommon.BluetoothError, e:
                    msg = "findservices() couldn't get services for %s: %s" % \
                        (iobtdevice.getNameOrAddress(), str(e))
                    warnings.warn(msg)
                    # or should I use cached services instead of warning?
                    # but sometimes the cached ones are totally wrong.
                    
            # if searching for RFCOMM, exclude OBEX services
            if servicetype == _lightbluecommon.RFCOMM:
                uuidbad = _macutil.PROTO_UUIDS.get(_lightbluecommon.OBEX)
            else:
                uuidbad = None
                    
            filtered = _searchservices(iobtdevice, name=name, 
                uuid=_macutil.PROTO_UUIDS.get(servicetype), 
                uuidbad=uuidbad)
            
            #print "unfiltered:", iobtdevice.getServices()
            services.extend([_getservicetuple(s) for s in filtered])
        finally:            
            # close baseband connection (not sure if this is necessary, but 
            # sometimes the transport connection seems to stay open?)
            iobtdevice.closeConnection()
        
    return services


def finddevicename(address, usecache=True):
    if not _lightbluecommon._isbtaddr(address):
        raise TypeError("%s is not a valid bluetooth address" % str(address))

    if address == gethostaddr():
        return _gethostname()

    device = _IOBluetooth.IOBluetoothDevice.withAddressString_(address)
    if usecache:
        name = device.getName()
        if name is not None:
            return name
    
    # do name request with timeout of 10 seconds    
    result = device.remoteNameRequest_withPageTimeout_(None, 10000)
    if result == _macutil.kIOReturnSuccess:
        return device.getName()
    raise _lightbluecommon.BluetoothError(
        "Could not find device name for %s" % address)      


### local device ###

def gethostaddr():
    addr = _LightAquaBlue.BBLocalDevice.getAddressString()
    if addr is not None:
        # PyObjC returns all strings as unicode, but the address doesn't need 
        # to be unicode cos it's just hex values
        return _macutil.formatdevaddr(addr)
    raise _lightbluecommon.BluetoothError("Cannot read local device address")                            


def gethostclass():
    cod = _LightAquaBlue.BBLocalDevice.getClassOfDevice()
    if cod != -1:
        return int(cod)
    raise _lightbluecommon.BluetoothError("Cannot read local device class")                            


def _gethostname():
    name = _LightAquaBlue.BBLocalDevice.getName()
    if name is not None:
        return name
    raise _lightbluecommon.BluetoothError("Cannot read local device name")                            
    
        
### socket ###

def socket(proto=_lightbluecommon.RFCOMM):
    return _bluetoothsockets._getsocketobject(proto)
        
### advertising services ###


def advertise(name, sock, servicetype):
    if not isinstance(name, types.StringTypes):
        raise TypeError("name must be string, was %s" % \
            type(name))
        
    # raises exception if socket is not bound
    boundchannelID = sock._getport()
    
    # advertise the service
    if servicetype == _lightbluecommon.RFCOMM:
        try:
            result, finalchannelID, servicerecordhandle = _LightAquaBlue.BBServiceAdvertiser.addRFCOMMServiceDictionary_withName_UUID_channelID_serviceRecordHandle_(
                _LightAquaBlue.BBServiceAdvertiser.serialPortProfileDictionary(), 
                name, 
                None, None, None)
        except:
            result, finalchannelID, servicerecordhandle = _LightAquaBlue.BBServiceAdvertiser.addRFCOMMServiceDictionary_withName_UUID_channelID_serviceRecordHandle_(
                _LightAquaBlue.BBServiceAdvertiser.serialPortProfileDictionary(), 
                name, 
                None)       
    elif servicetype == _lightbluecommon.OBEX:
        try:
            result, finalchannelID, servicerecordhandle = _LightAquaBlue.BBServiceAdvertiser.addRFCOMMServiceDictionary_withName_UUID_channelID_serviceRecordHandle_(
                _LightAquaBlue.BBServiceAdvertiser.objectPushProfileDictionary(), 
                name, 
                None, None, None)
        except:
            result, finalchannelID, servicerecordhandle = _LightAquaBlue.BBServiceAdvertiser.addRFCOMMServiceDictionary_withName_UUID_channelID_serviceRecordHandle_(
                _LightAquaBlue.BBServiceAdvertiser.objectPushProfileDictionary(), 
                name, 
                None)       
    else:
        raise ValueError("servicetype must be either RFCOMM or OBEX")

    if result != _macutil.kIOReturnSuccess:
        raise _lightbluecommon.BluetoothError(
            result, "Error advertising service")
    if boundchannelID != finalchannelID:
        msg = "socket bound to unavailable channel (%d), " % boundchannelID +\
              "use channel value of 0 to bind to dynamically assigned channel"
        raise _lightbluecommon.BluetoothError(msg)
    
    # note service record handle, so that the service can be stopped later
    __advertised[id(sock)] = servicerecordhandle
    
    
def stopadvertise(sock):
    if sock is None:
        raise TypeError("Given socket is None")

    servicerecordhandle = __advertised.get(id(sock))
    if servicerecordhandle is None:
        raise _lightbluecommon.BluetoothError("no service advertised")
        
    result = _LightAquaBlue.BBServiceAdvertiser.removeService_(servicerecordhandle)
    if result != _macutil.kIOReturnSuccess:
        raise _lightbluecommon.BluetoothError(
            result, "Error stopping advertising of service")


### GUI ###


def selectdevice():
    import _IOBluetoothUI
    gui = _IOBluetoothUI.IOBluetoothDeviceSelectorController.deviceSelector()
    
    # try to bring GUI to foreground by setting it as floating panel
    # (if this is called from pyobjc app, it would automatically be in foreground)
    try:
        gui.window().setFloatingPanel_(True)
    except:
        pass
    
    # show the window and wait for user's selection
    response = gui.runModal()   # problems here if transferring a lot of data??
    if response == AppKit.NSRunStoppedResponse:
        results = gui.getResults()
        if len(results) > 0:  # should always be > 0, but check anyway
            devinfo = _getdevicetuple(results[0])
    
            # sometimes the baseband connection stays open which causes 
            # problems with connections w so close it here, see if this fixes 
            # it        
            dev = _IOBluetooth.IOBluetoothDevice.withAddressString_(devinfo[0])
            if dev.isConnected(): 
                dev.closeConnection()        
        
            return devinfo
        
    # user cancelled selection
    return None


def selectservice():
    import _IOBluetoothUI
    gui = _IOBluetoothUI.IOBluetoothServiceBrowserController.serviceBrowserController_(
            _macutil.kIOBluetoothServiceBrowserControllerOptionsNone)
            
    # try to bring GUI to foreground by setting it as floating panel
    # (if this is called from pyobjc app, it would automatically be in foreground)
    try:
        gui.window().setFloatingPanel_(True)
    except:
        pass            
            
    # show the window and wait for user's selection        
    response = gui.runModal()
    if response == AppKit.NSRunStoppedResponse:  
        results = gui.getResults()
        if len(results) > 0:  # should always be > 0, but check anyway    
            serviceinfo = _getservicetuple(results[0])
            
            # sometimes the baseband connection stays open which causes 
            # problems with connections ... so close it here, see if this fixes 
            # it        
            dev = _IOBluetooth.IOBluetoothDevice.deviceWithAddressString_(serviceinfo[0])        
            if dev.isConnected(): 
                dev.closeConnection()        
            
            return serviceinfo
        
    # user cancelled selection        
    return None
    

### classes ###

class _SDPQueryRunner(Foundation.NSObject):
    """
    Convenience class for performing a synchronous or asynchronous SDP query
    on an IOBluetoothDevice.
    """

    def query(self, device, timeout=10.0):
        # do SDP query
        err = device.performSDPQuery_(self)
        if err != _macutil.kIOReturnSuccess:
            raise _lightbluecommon.BluetoothError(err, self._errmsg(device))
        
        # performSDPQuery_ is async, so block-wait
        self._queryresult = None
        if not _macutil.waituntil(lambda: self._queryresult is not None,
                                          timeout):
            raise _lightbluecommon.BluetoothError(
                "Timed out getting services for %s" % \
                    device.getNameOrAddress())
        # query is now complete
        if self._queryresult != _macutil.kIOReturnSuccess:  
            raise _lightbluecommon.BluetoothError(
                self._queryresult, self._errmsg(device))
        
    def sdpQueryComplete_status_(self, device, status):
        # can't raise exception during a callback, so just keep the err value
        self._queryresult = status
        _macutil.interruptwait()
    sdpQueryComplete_status_ = objc.selector(
        sdpQueryComplete_status_, signature="v@:@i")    # accept object, int
            
    def _errmsg(self, device):
        return "Error getting services for %s" % device.getNameOrAddress()
            
            
class _SyncDeviceInquiry(object):

    def __init__(self):
        super(_SyncDeviceInquiry, self).__init__()        
            
        self._inquiry = _AsyncDeviceInquiry.alloc().init()
        self._inquiry.cb_completed = self._inquirycomplete
        
        self._inquiring = False
        
    def run(self, getnames, duration):
        if self._inquiring:
            raise _lightbluecommon.BluetoothError(
                "Another inquiry in progress")
            
        # set inquiry attributes
        self._inquiry.updatenames = getnames
        self._inquiry.length = duration
        
        # start the inquiry
        err = self._inquiry.start()
        if err != _macutil.kIOReturnSuccess:
            raise _lightbluecommon.BluetoothError(
                err, "Error starting device inquiry")      
            
        # if error occurs during inquiry, set _inquiryerr to the error code
        self._inquiryerr = _macutil.kIOReturnSuccess
        
        # wait until the inquiry is complete
        self._inquiring = True
        _macutil.waituntil(lambda: not self._inquiring)
        
        # if error occured during inquiry, raise exception
        if self._inquiryerr != _macutil.kIOReturnSuccess:
            raise _lightbluecommon.BluetoothError(self._inquiryerr, 
                "Error during device inquiry")
            
    def getfounddevices(self):
        # return as list of device-info tuples
        return [_getdevicetuple(device) for device in \
                    self._inquiry.getfounddevices()]
        
    def _inquirycomplete(self, err, aborted):
        if err != 188:      # no devices found
            self._inquiryerr = err
        self._inquiring = False
        _macutil.interruptwait()
        
    def __del__(self):
        self._inquiry.__del__()
        super(_SyncDeviceInquiry, self).__del__()

class _AsyncPair(Foundation.NSObject):
    def initWithAddress_(self, address):
        try:
            attr = _IOBluetooth.IOBluetoothDevicePair
        except AttributeError:
            raise ImportError("Cannot find IOBluetoothDevicePair class " +\
                "to perform device discovery. This class was introduced in " +\
                "Mac OS X 10.7, are you running an earlier version?")
        self = super(_AsyncPair, self).init()
        self.device = _IOBluetooth.IOBluetoothDevice.withAddressString_(address)
        if not self.device:
            raise _lightbluecommon.BluetoothError(
                "Could not find device name for %s" % address) 
        self.pair = _IOBluetooth.IOBluetoothDevicePair.pairWithDevice_(self.device)
        if not self.pair:
            raise _lightbluecommon.BluetoothError(
                "Could not create pairing state machine.") 
        self.pair.setDelegate_(self)
        return self

    def start(self):
        result = self.pair.start()
        if result != _macutil.kIOReturnSuccess:
            return (None, result)
        self.pairResult = None
        self.error = None
        timeout = 10.0
        if not _macutil.waituntil(lambda: self.pairResult is not None or self.error is not None, timeout):
            self.error = -99
            print "Timed out pairing with %s" % self.pair.device().addressString()
        self.pair.setDelegate_(None)
        self.pair = None
        self.device = None
        return (self.pairResult, self.error)

    # - (void) devicePairingStarted:(id)sender;
    def devicePairingStarted_(self, sender):
        print "devicePairingStarted_"

    # - (void) devicePairingConnecting:(id)sender;
    def devicePairingConnecting_(self, sender):
        print "devicePairingConnecting_"

    # - (void) devicePairingPINCodeRequest:(id)sender;
    def devicePairingPINCodeRequest_(self, sender):
        print "devicePairingPINCodeRequest_ unsupported!"

    # - (void) devicePairingUserConfirmationRequest:(id)sender
    #                                  numericValue:(BluetoothNumericValue)numericValue;
    def devicePairingUserConfirmationRequest_numericValue_(self, sender, numericValue):
        print "devicePairingUserConfirmationRequest_numericValue_: %lu" % numericValue
        if self.pair:
            self.pair.replyUserConfirmation_(True)
        self.pairResult = numericValue
        _macutil.interruptwait()
    devicePairingUserConfirmationRequest_numericValue_ = objc.selector(
        devicePairingUserConfirmationRequest_numericValue_, signature="v@:@I")

    # - (void) devicePairingUserPasskeyNotification:(id)sender
    #                                       passkey:(BluetoothPasskey)passkey;
    def devicePairingUserPasskeyNotification_passkey_(self, sender, passkey):
        self.pairResult = passkey
        print "devicePairingUserPasskeyNotification_passkey_: %lu" % passkey
        _macutil.interruptwait()
    devicePairingUserPasskeyNotification_passkey_ = objc.selector(
        devicePairingUserPasskeyNotification_passkey_, signature="v@:@I")

    # - (void) devicePairingFinished:(id)sender
    #                          error:(IOReturn)error;
    def devicePairingFinished_error_(self, sender, error):
        if error:
            print "devicePairingFinished_error_: %lu" % error
            self.error = error
        _macutil.interruptwait()
    devicePairingFinished_error_ = objc.selector(
        devicePairingFinished_error_, signature="v@:@i")

    # - (void) deviceSimplePairingComplete:(id)sender
    #                               status:(BluetoothHCIEventStatus)status
    def deviceSimplePairingComplete_status_(self, sender, status):
        print "deviceSimplePairingComplete_status_: %u" % status
    deviceSimplePairingComplete_status_ = objc.selector(
        deviceSimplePairingComplete_status_, signature="v@:@C")

# Wrapper around IOBluetoothDeviceInquiry, with python callbacks that you can
# set to receive callbacks when the inquiry is started or stopped, or when it
# finds a device.
#
# This discovery doesn't block, so it could be used in a PyObjC application
# that is running an event loop.
#
# Properties:
#   - 'length': the inquiry length (seconds)
#   - 'updatenames': whether to update device names during the inquiry
#     (i.e. perform remote name requests, which will take a little longer)
#
class _AsyncDeviceInquiry(Foundation.NSObject): 

    # NSObject init, not python __init__
    def init(self):
        try:
            attr = _IOBluetooth.IOBluetoothDeviceInquiry
        except AttributeError:
            raise ImportError("Cannot find IOBluetoothDeviceInquiry class " +\
                "to perform device discovery. This class was introduced in " +\
                "Mac OS X 10.4, are you running an earlier version?")
    
        self = super(_AsyncDeviceInquiry, self).init()
        self._inquiry = \
            _IOBluetooth.IOBluetoothDeviceInquiry.inquiryWithDelegate_(self)
        
        # callbacks
        self.cb_started = None
        self.cb_completed = None
        self.cb_founddevice = None
        
        return self
    
    # length property
    def _setlength(self, length):
        self._inquiry.setInquiryLength_(length)
    length = property(
            lambda self: self._inquiry.inquiryLength(),
            _setlength)
            
    # updatenames property
    def _setupdatenames(self, update):
        self._inquiry.setUpdateNewDeviceNames_(update)
    updatenames = property(
            lambda self: self._inquiry.updateNewDeviceNames(),
            _setupdatenames)

    # returns error code
    def start(self):
        return self._inquiry.start()

    # returns error code
    def stop(self):
        return self._inquiry.stop()
        
    # returns list of IOBluetoothDevice objects
    def getfounddevices(self):
        return self._inquiry.foundDevices()
        
    def deviceInquiryDeviceNameUpdated_device_devicesRemaining_(self, sender, device, devicesRemaining):
        pass

    def deviceInquiryUpdatingDeviceNamesStarted_devicesRemaining_(self, sender, devicesRemaining):
        pass

    def __del__(self):
        super(_AsyncDeviceInquiry, self).dealloc()
        
        
    #
    # delegate methods follow (these are called by the internal 
    # IOBluetoothDeviceInquiry object when inquiry events occur)
    #        
        
    # - (void)deviceInquiryDeviceFound:(IOBluetoothDeviceInquiry*)sender 
    #                           device:(IOBluetoothDevice*)device;
    def deviceInquiryDeviceFound_device_(self, inquiry, device):
        if self.cb_founddevice:
            self.cb_founddevice(device)
    deviceInquiryDeviceFound_device_ = objc.selector(
        deviceInquiryDeviceFound_device_, signature="v@:@@")
    
    # - (void)deviceInquiryComplete:error:aborted;
    def deviceInquiryComplete_error_aborted_(self, inquiry, err, aborted):
        if self.cb_completed:
            self.cb_completed(err, aborted)
    deviceInquiryComplete_error_aborted_ = objc.selector(
        deviceInquiryComplete_error_aborted_, signature="v@:@iZ")
             
    # - (void)deviceInquiryStarted:(IOBluetoothDeviceInquiry*)sender;             
    def deviceInquiryStarted_(self, inquiry):
        if self.cb_started:
            self.cb_started()
        
        
### utility methods ###

    
def _searchservices(device, name=None, uuid=None, uuidbad=None):
    """
    Searches the given IOBluetoothDevice using the specified parameters.
    Returns an empty list if the device has no services.
    
    uuid should be IOBluetoothSDPUUID object.
    """
    if not isinstance(device, _IOBluetooth.IOBluetoothDevice):
        raise ValueError("device must be IOBluetoothDevice, was %s" % \
            type(device))
    
    services = []
    allservices = device.getServices()
    if uuid:
        gooduuids = (uuid, )
    else:
        gooduuids = ()
    if uuidbad:
        baduuids = (uuidbad, )
    else:
        baduuids = ()
    
    if allservices is not None:
        for s in allservices:
            if gooduuids and not s.hasServiceFromArray_(gooduuids):
                continue
            if baduuids and s.hasServiceFromArray_(baduuids):
                continue
            if name is None or s.getServiceName() == name:
                services.append(s)
    return services    

def _getdevicetuple(iobtdevice):
    """
    Returns an (addr, name, COD) device tuple from a IOBluetoothDevice object.
    """
    addr = _macutil.formatdevaddr(iobtdevice.getAddressString())
    name = iobtdevice.getName()
    cod = iobtdevice.getClassOfDevice()
    return (addr, name, cod)

        
def _getservicetuple(servicerecord):
    """
    Returns a (device-addr, service-channel, service-name) tuple from the given 
    IOBluetoothSDPServiceRecord.
    """
    addr = _macutil.formatdevaddr(servicerecord.getDevice().getAddressString())            
    name = servicerecord.getServiceName()
    try:
        result, channel = servicerecord.getRFCOMMChannelID_(None) # pyobjc 2.0
    except TypeError:
        result, channel = servicerecord.getRFCOMMChannelID_()
    if result != _macutil.kIOReturnSuccess:
        try:
            result, channel = servicerecord.getL2CAPPSM_(None) # pyobjc 2.0
        except:
            result, channel = servicerecord.getL2CAPPSM_()            
        if result != _macutil.kIOReturnSuccess:    
            channel = None
    return (addr, channel, name)    
         
