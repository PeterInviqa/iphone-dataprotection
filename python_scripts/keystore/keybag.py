import hashlib
import hmac
import struct
from util.tlv import loopTLVBlocks, tlvToDict
from crypto.aeswrap import AESUnwrap
from pprint import pprint
from crypto.PBKDF2 import PBKDF2
from util.bplist import BPlistReader
from crypto.aes import AESdecryptCBC

KEYBAG_TAGS = ["VERS", "TYPE", "UUID", "HMCK", "WRAP", "SALT", "ITER"]
CLASSKEY_TAGS = ["CLAS","WRAP","WPKY"]  #UUID
KEYBAG_TYPES = ["System", "Backup", "Escrow"]
SYSTEM_KEYBAG = 0
BACKUP_KEYBAG = 1
ESCROW_KEYBAG = 2

WRAP_DEVICE = 1
WRAP_PASSCODE = 2

"""
    device key : key 0x835
"""
class Keybag(object):
    def __init__(self, data):
        self.type = None
        self.uuid = None
        self.wrap = None
        self.deviceKey = None
        self.unlocked = False
        self.attrs = {}
        self.classKeys = {}
        self.parseBinaryBlob(data)

    @staticmethod
    def getSystemkbfileWipeID(filename):
        mkb = BPlistReader.plistWithFile(filename)
        return mkb["_MKBWIPEID"]

    @staticmethod
    def createWithSystemkbfile(filename, wipeablekey, deviceKey=None):
        mkb = BPlistReader.plistWithFile(filename)
        decryptedPlist  = AESdecryptCBC(mkb["_MKBPAYLOAD"], wipeablekey, mkb["_MKBIV"], padding=True)
        decryptedPlist = BPlistReader.plistWithString(decryptedPlist)
        blob = decryptedPlist["KeyBagKeys"]
        return Keybag.createWithDataSignBlob(blob, deviceKey)
    
    @staticmethod
    def createWithDataSignBlob(blob, deviceKey=None):
        keybag = tlvToDict(blob)
        
        kb = Keybag(keybag["DATA"])
        kb.deviceKey = deviceKey
        
        if len(keybag.get("SIGN", "")):
            hmackey = AESUnwrap(deviceKey, kb.attrs["HMCK"])
            #hmac key and data are swapped (on purpose or by mistake ?)
            sigcheck = hmac.new(keybag["DATA"], hmackey, hashlib.sha1).digest()
            if sigcheck == keybag.get("SIGN", ""):
                print "Keybag: SIGN check OK"
            else:
                print "Keybag: SIGN check FAIL"
        return kb
        
    def parseBinaryBlob(self, data):
        currentClassKey = None
        
        for tag, data in loopTLVBlocks(data):
            if len(data) == 4:
                data = struct.unpack(">L", data)[0]
            if tag == "TYPE":
                self.type = data
                if self.type > 2:
                    print "FAIL: keybag type > 2"
            elif tag == "UUID" and self.uuid is None:
                self.uuid = data
            elif tag == "WRAP" and self.wrap is None:
                self.wrap = data
            elif tag == "UUID":
                if currentClassKey:
                    self.classKeys[currentClassKey["CLAS"]] = currentClassKey
                currentClassKey = {"UUID": data}
            elif tag in CLASSKEY_TAGS:
                currentClassKey[tag] = data
            else:
                self.attrs[tag] = data
        self.classKeys[currentClassKey["CLAS"]] = currentClassKey

    def getPasscodekeyFromPasscode(self, passcode):
        if self.type == BACKUP_KEYBAG:
            return PBKDF2(passcode, self.attrs["SALT"], iterations=self.attrs["ITER"]).read(32)
        else:
            #Warning, need to run derivation on device with this result
            return PBKDF2(passcode, self.attrs["SALT"], iterations=1).read(32)
    
    def unlockBackupKeybagWithPasscode(self, passcode):
        if self.type != BACKUP_KEYBAG:
            print "unlockBackupKeybagWithPasscode: not a backup keybag"
            return False
        return self.unlockWithPasscodeKey(self.getPasscodekeyFromPasscode(passcode))
             
    def unlockWithPasscodeKey(self, passcodekey):
        if self.type != BACKUP_KEYBAG:
            if not self.deviceKey:
                print "ERROR, need device key to unlock keybag"
                return False

        for classkey in self.classKeys.values():
            k = classkey["WPKY"]
            if classkey["WRAP"] & WRAP_PASSCODE:
                k = AESUnwrap(passcodekey, classkey["WPKY"])
                if not k:
                    return False
            if classkey["WRAP"] & WRAP_DEVICE:
                if not self.deviceKey:
                    continue
                k = AESdecryptCBC(k, self.deviceKey)
            
            classkey["KEY"] = k
        self.unlocked =  True
        return True

    def unwrapKeyForClass(self, clas, data):
        if not self.classKeys[clas].has_key("KEY"):
            print "ERROR: keybag locked"
            return ""
        ck = self.classKeys[clas]["KEY"]
        return AESUnwrap(ck, data)
    
    def printClassKeys(self):
        print "Keybag type : %s keybag (%d)" % (KEYBAG_TYPES[self.type], self.type)
        print "Keybag version : %d" % self.attrs["VERS"]
        print "Class\tWRAP\tKey"
        for ck in self.classKeys.values():
            print "%s\t%s\t%s" % (ck["CLAS"], ck["WRAP"], ck.get("KEY","").encode("hex"))
    
    def getClearClassKeysDict(self):
        if self.unlocked:
            d = {}
            for ck in self.classKeys.values():
                d["%d" % ck["CLAS"]] = ck.get("KEY","").encode("hex")
            return d
        