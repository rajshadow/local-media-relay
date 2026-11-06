"""DLNA device descriptions, SOAP responses, and media catalog."""
import base64
import html
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from library import IMAGE_EXTS, VIDEO_EXTS, is_media

DLNA_FEATURES = 'DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000'


def mime_for(path: Path) -> str:
    return {
        '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mkv': 'video/x-matroska',
        '.webm': 'video/webm', '.mov': 'video/quicktime',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
    }[path.suffix.lower()]


def protocol_info(path: Path) -> str:
    return f'http-get:*:{mime_for(path)}:{DLNA_FEATURES}'


def didl_escape(text: str) -> str:
    return html.escape(text, quote=True)


def didl_document(entries: list[str]) -> str:
    return (
        '<DIDL-Lite '
        'xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" '
        'xmlns:sec="http://www.sec.co.kr/dlna">'
        + ''.join(entries)
        + '</DIDL-Lite>'
    )


def soap_value(root: ET.Element, name: str, default: str = '') -> str:
    for elem in root.iter():
        if elem.tag.rsplit('}', 1)[-1] == name:
            return elem.text or default
    return default


def soap_response(action: str, inner: str, service: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:{action}Response xmlns:u="urn:schemas-upnp-org:service:{service}:1">'
        f'{inner}'
        f'</u:{action}Response>'
        '</s:Body>'
        '</s:Envelope>'
    )
    return body.encode('utf-8')


def soap_fault(code: int, description: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        '<s:Body><s:Fault>'
        '<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring>'
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f'<errorCode>{code}</errorCode><errorDescription>{didl_escape(description)}</errorDescription>'
        '</UPnPError></detail>'
        '</s:Fault></s:Body></s:Envelope>'
    )
    return body.encode('utf-8')


def device_xml(udn) -> bytes:
    xml = f'''<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
    <friendlyName>Local Media Relay</friendlyName>
    <manufacturer>Local Media Relay</manufacturer>
    <manufacturerURL>http://127.0.0.1/</manufacturerURL>
    <modelDescription>Local Media Relay MediaServer</modelDescription>
    <modelName>Local Media Relay</modelName>
    <modelNumber>0.1</modelNumber>
    <UDN>{udn}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/dlna/content-directory.xml</SCPDURL>
        <controlURL>/dlna/content-directory/control</controlURL>
        <eventSubURL/>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/dlna/connection-manager.xml</SCPDURL>
        <controlURL>/dlna/connection-manager/control</controlURL>
        <eventSubURL/>
      </service>
    </serviceList>
  </device>
</root>'''
    return xml.encode('utf-8')


CONTENT_DIRECTORY_SCPD = b'''<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>Browse</name><argumentList>
      <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
      <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
      <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
      <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
      <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
      <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
      <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
      <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSearchCapabilities</name><argumentList>
      <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSortCapabilities</name><argumentList>
      <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetSystemUpdateID</name><argumentList>
      <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>X_SetBookmark</name><argumentList>
      <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
      <argument><name>PosSecond</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_PosSecond</relatedStateVariable></argument>
    </argumentList></action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>ContainerUpdateIDs</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_PosSecond</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
  </serviceStateTable>
</scpd>'''


CONNECTION_MANAGER_SCPD = b'''<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>GetProtocolInfo</name><argumentList>
      <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
      <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetCurrentConnectionIDs</name><argumentList>
      <argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>
    </argumentList></action>
    <action><name>GetCurrentConnectionInfo</name><argumentList>
      <argument><name>ConnectionID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
      <argument><name>RcsID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>
      <argument><name>AVTransportID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>
      <argument><name>ProtocolInfo</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>
      <argument><name>PeerConnectionManager</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>
      <argument><name>PeerConnectionID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
      <argument><name>Direction</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>
      <argument><name>Status</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>
    </argumentList></action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name><dataType>i4</dataType></stateVariable>
  </serviceStateTable>
</scpd>'''


def connection_manager_action(action: str, body: bytes) -> tuple[int, bytes]:
    if action == 'GetProtocolInfo':
        source = (
            f'http-get:*:video/mp4:{DLNA_FEATURES},'
            f'http-get:*:video/x-matroska:{DLNA_FEATURES},'
            f'http-get:*:video/webm:{DLNA_FEATURES},'
            f'http-get:*:video/quicktime:{DLNA_FEATURES},'
            f'http-get:*:image/jpeg:{DLNA_FEATURES},'
            f'http-get:*:image/png:{DLNA_FEATURES}'
        )
        return 200, soap_response(action, f'<Source>{source}</Source><Sink></Sink>', 'ConnectionManager')
    if action == 'GetCurrentConnectionIDs':
        return 200, soap_response(action, '<ConnectionIDs>0</ConnectionIDs>', 'ConnectionManager')
    if action == 'GetCurrentConnectionInfo':
        try:
            connection_id = int(soap_value(ET.fromstring(body), 'ConnectionID'))
        except (ET.ParseError, ValueError):
            return 500, soap_fault(402, 'Invalid arguments')
        if connection_id != 0:
            return 500, soap_fault(706, 'No such connection')
        inner = (
            '<RcsID>-1</RcsID><AVTransportID>-1</AVTransportID>'
            '<ProtocolInfo></ProtocolInfo><PeerConnectionManager></PeerConnectionManager>'
            '<PeerConnectionID>-1</PeerConnectionID><Direction>Output</Direction>'
            '<Status>Unknown</Status>'
        )
        return 200, soap_response(action, inner, 'ConnectionManager')
    return 500, soap_fault(401, 'Invalid action')


def token(key):
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip('=') if key else '0'


def key_for_id(identifier):
    if identifier == '0':
        return ''
    return base64.b64decode(identifier + '=' * (-len(identifier) % 4),
                            altchars=b'-_', validate=True).decode()


class ContentDirectory:
    def __init__(self, library, base, boot_id, thumbnails=None, checkpoints=None):
        self.library = library
        self.thumbnails = thumbnails
        self.checkpoints = checkpoints
        self.base = base
        self.update_id = boot_id & 0xffffffff
        self.catalog_lock = Lock()
        self.watched = {
            str(index): self.directory_stamp(str(index)) for index in range(len(library.roots))
        }

    def directory_stamp(self, key):
        try:
            folder = self.library.path(key)
            if not folder.is_dir():
                return None
            info = folder.stat()
            return info.st_ino, info.st_mtime_ns
        except (OSError, ValueError):
            return None

    def catalog_update(self, folder=None):
        # Poll only the roots and directories clients have listed. This avoids
        # recursively scanning large libraries on every GetSystemUpdateID.
        with self.catalog_lock:
            changed = False
            for key, prior in self.watched.items():
                current = self.directory_stamp(key)
                if current != prior:
                    self.watched[key] = current
                    changed = True
            if folder is not None and folder not in self.watched and len(self.watched) < 1024:
                stamp = self.directory_stamp(folder)
                if stamp is not None:
                    self.watched[folder] = stamp
            if changed:
                self.update_id = (self.update_id + 1) & 0xffffffff
            return self.update_id

    def media_url(self, key):
        return f'{self.base}/media/{quote(key)}'

    def didl(self, key, client=''):
        identifier = token(key)
        parent = token(key.rpartition('/')[0]) if key else '-1'
        title = html.escape(self.library.title(key))
        if self.library.is_dir(key):
            return (f'<container id="{identifier}" parentID="{parent}" restricted="1">'
                    f'<dc:title>{title}</dc:title><upnp:class>object.container.storageFolder</upnp:class>'
                    '</container>')
        path = self.library.path(key)
        if not path.is_file() or not is_media(path):
            raise ValueError('Unsupported file')
        url = html.escape(self.media_url(key))
        protocol = html.escape(protocol_info(path), quote=True)
        media_class = 'imageItem.photo' if path.suffix.lower() in IMAGE_EXTS else 'videoItem'
        bookmark = ''
        if self.checkpoints is not None and client and path.suffix.lower() in VIDEO_EXTS:
            position = round(self.checkpoints.get(path, client) * 1000)
            folder = html.escape(path.stem.replace(',', ' '))
            bookmark = f'<sec:dcmInfo>CREATIONDATE=0,FOLDER={folder},BM={position}</sec:dcmInfo>'
        artwork = ''
        if self.thumbnails is not None:
            thumbnail = self.thumbnails.request(path)
            if thumbnail is not None or self.thumbnails.ffmpeg:
                artwork = (f'<upnp:albumArtURI>{html.escape(self.base)}/dlna/thumb/{identifier}.jpg'
                           '</upnp:albumArtURI>')
        return (f'<item id="{identifier}" parentID="{parent}" restricted="1">'
                f'<dc:title>{title}</dc:title><upnp:class>object.item.{media_class}</upnp:class>{artwork}{bookmark}'
                f'<res protocolInfo="{protocol}" size="{path.stat().st_size}">{url}</res></item>')

    def content_action(self, action, body, client=''):
        service = 'ContentDirectory'
        simple = {
            'GetSearchCapabilities': '<SearchCaps></SearchCaps>',
            'GetSortCapabilities': '<SortCaps></SortCaps>',
        }
        if action == 'GetSystemUpdateID':
            return 200, soap_response(action, f'<Id>{self.catalog_update()}</Id>', service)
        if action in simple:
            return 200, soap_response(action, simple[action], service)
        if action == 'X_SetBookmark' and self.checkpoints is not None and client:
            try:
                root = ET.fromstring(body)
                key = key_for_id(soap_value(root, 'ObjectID'))
                path = self.library.path(key)
                if path is None or not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
                    return 500, soap_fault(701, 'No such object')
                seconds = float(soap_value(root, 'PosSecond')) / 1000
                changed = self.checkpoints.set(path, client, seconds)
            except (ValueError, ET.ParseError):
                return 500, soap_fault(402, 'Invalid arguments')
            except OSError:
                return 500, soap_fault(501, 'Checkpoint could not be saved')
            if changed:
                with self.catalog_lock:
                    self.update_id = (self.update_id + 1) & 0xffffffff
            return 200, soap_response(action, '', service)
        if action != 'Browse':
            return 500, soap_fault(401, 'Invalid action')
        try:
            root = ET.fromstring(body)
            object_id = soap_value(root, 'ObjectID', '0')
            key = key_for_id(object_id)
            flag = soap_value(root, 'BrowseFlag')
            update_id = self.catalog_update(key if key and flag == 'BrowseDirectChildren' else None)
            if flag == 'BrowseMetadata':
                entries = [self.didl(key, client)]
                total = 1
            elif flag == 'BrowseDirectChildren':
                start = max(0, int(soap_value(root, 'StartingIndex', '0')))
                count = max(0, int(soap_value(root, 'RequestedCount', '0')))
                children = self.library.children(key)
                total = len(children)
                entries = [self.didl(child, client) for child in children[start:start + count if count else None]]
            else:
                return 500, soap_fault(402, 'Invalid arguments')
        except (ValueError, OSError, ET.ParseError):
            return 500, soap_fault(701, 'No such object')
        result = didl_escape(didl_document(entries))
        inner = (f'<Result>{result}</Result><NumberReturned>{len(entries)}</NumberReturned>'
                 f'<TotalMatches>{total}</TotalMatches><UpdateID>{update_id}</UpdateID>')
        return 200, soap_response(action, inner, service)
