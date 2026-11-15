"""
Python Wechaty - https://github.com/wechaty/python-wechaty

Authors:    Huan LI (李卓桓) <https://github.com/huan>
            Jingjing WU (吴京京) <https://github.com/wj-Mcat>

2020-now @ Copyright Wechaty

Licensed under the Apache License, Version 2.0 (the 'License');
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an 'AS IS' BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Callable, Optional, List
from dataclasses import asdict
import requests

from wechaty_grpc.wechaty import (
    PuppetStub,
)
from wechaty_grpc.wechaty.puppet import (
    MessageFileResponse,
    MessageImageResponse
)
# pylint: disable=E0401
from grpclib.client import Channel
# pylint: disable=E0401
from grpclib.const import Status as GrpcStatus
# pylint: disable=E0401
from grpclib.exceptions import GRPCError, StreamTerminatedError
# pylint: disable=E0401
from pyee import AsyncIOEventEmitter
from wechaty_puppet.schemas.types import PayloadType

from wechaty_puppet import (
    EventScanPayload,
    ScanStatus,

    EventReadyPayload,

    EventDongPayload,
    EventRoomTopicPayload,
    EventRoomLeavePayload,
    EventRoomJoinPayload,
    EventRoomInvitePayload,

    EventMessagePayload,
    EventLogoutPayload,
    EventLoginPayload,
    EventFriendshipPayload,
    EventHeartbeatPayload,
    EventErrorPayload,
    FileBox, RoomMemberPayload, RoomPayload, RoomInvitationPayload,
    RoomQueryFilter, FriendshipPayload, ContactPayload, MessagePayload,
    MessageQueryFilter,

    ImageType,
    EventType,
    MessageType,
    Puppet,
    PuppetOptions,
    MiniProgramPayload,
    UrlLinkPayload,

    get_logger
)

from wechaty_puppet.exceptions import (
    WechatyPuppetConfigurationError,
    WechatyPuppetError,
    WechatyPuppetGrpcError,
    WechatyPuppetOperationError,
    WechatyPuppetPayloadError
)

from wechaty_puppet_service.config import (
    get_endpoint,
    get_token,
)
from wechaty_puppet_service.utils import (
    extract_host_and_port,
    ping_endpoint,
    message_emoticon
)

log = get_logger('PuppetService')


def _map_message_type(message_payload: MessagePayload) -> MessagePayload:
    """
    get messageType value which is ts-wechaty-puppet type from service server,
        but is MessageType. so we should map it to MessageType from wechaty-grpc
    target MessageType Enum:
        MESSAGE_TYPE_UNSPECIFIED  = 0;

       MESSAGE_TYPE_ATTACHMENT   = 1;
       MESSAGE_TYPE_AUDIO        = 2;
       MESSAGE_TYPE_CONTACT      = 3;
       MESSAGE_TYPE_EMOTICON     = 4;
       MESSAGE_TYPE_IMAGE        = 5;
       MESSAGE_TYPE_TEXT         = 6;
       MESSAGE_TYPE_VIDEO        = 7;
       MESSAGE_TYPE_CHAT_HISTORY = 8;
       MESSAGE_TYPE_LOCATION     = 9;
       MESSAGE_TYPE_MINI_PROGRAM = 10;
       MESSAGE_TYPE_TRANSFER     = 11;
       MESSAGE_TYPE_RED_ENVELOPE = 12;
       MESSAGE_TYPE_RECALLED     = 13;
       MESSAGE_TYPE_URL          = 14;

    source MessageType Enum:
        export enum MessageType {
          Unknown = 0,

          Attachment=1,     // Attach(6),
          Audio=2,          // Audio(1), Voice(34)
          Contact=3,        // ShareCard(42)
          ChatHistory=4,    // ChatHistory(19)
          Emoticon=5,       // Sticker: Emoticon(15), Emoticon(47)
          Image=6,          // Img(2), Image(3)
          Text=7,           // Text(1)
          Location=8,       // Location(48)
          MiniProgram=9,    // MiniProgram(33)
          GroupNote=10,      // GroupNote(53)
          Transfer=11,       // Transfers(2000)
          RedEnvelope=12,    // RedEnvelopes(2001)
          Recalled=13,       // Recalled(10002)
          Url=14,            // Url(5)
          Video=15,          // Video(4), Video(43)
        }
    :return:

    #
    """
    if isinstance(message_payload.type, int):
        map_container: List[MessageType] = [
            MessageType.MESSAGE_TYPE_UNSPECIFIED,
            MessageType.MESSAGE_TYPE_ATTACHMENT,
            MessageType.MESSAGE_TYPE_AUDIO,
            MessageType.MESSAGE_TYPE_CONTACT,
            MessageType.MESSAGE_TYPE_CHAT_HISTORY,
            MessageType.MESSAGE_TYPE_EMOTICON,
            MessageType.MESSAGE_TYPE_IMAGE,
            MessageType.MESSAGE_TYPE_TEXT,
            MessageType.MESSAGE_TYPE_LOCATION,
            MessageType.MESSAGE_TYPE_MINI_PROGRAM,
            MessageType.MESSAGE_TYPE_UNSPECIFIED,
            MessageType.MESSAGE_TYPE_TRANSFER,
            MessageType.MESSAGE_TYPE_RED_ENVELOPE,
            MessageType.MESSAGE_TYPE_RECALLED,
            MessageType.MESSAGE_TYPE_URL,
            MessageType.MESSAGE_TYPE_VIDEO]
        message_payload.type = map_container[message_payload.type]
    return message_payload


# pylint: disable=R0904,R0902
class PuppetService(Puppet):
    """
    grpc wechaty puppet implementation
    """

    #: how many times the event stream is reconnected before giving up
    EVENT_STREAM_MAX_RECONNECT_ATTEMPTS: int = 5
    #: the delay before the first reconnection attempt (in seconds)
    EVENT_STREAM_RECONNECT_BASE_DELAY: float = 0.2
    #: the upper bound of the (exponentially growing) reconnection delay
    EVENT_STREAM_RECONNECT_MAX_DELAY: float = 2.0
    #: how long stop() waits for the listener task to unwind (in seconds)
    EVENT_STREAM_STOP_TIMEOUT: float = 5.0
    #: how many event fingerprints are remembered to suppress replayed events
    EVENT_STREAM_DEDUPE_CACHE_SIZE: int = 512
    #: a stream which stayed alive for this long resets the reconnect budget
    EVENT_STREAM_STABLE_CONNECTION_SECONDS: float = 30.0

    def __init__(self, options: PuppetOptions, name: str = 'puppet_service'):
        """init PuppetService from options or environment

        Args:
            options (PuppetOptions): the configuration of PuppetService
            name (str, optional): [description]. Defaults to 'service_puppet'.

        Raises:
            WechatyPuppetConfigurationError: raise Error when configuration occur error
        """
        super().__init__(options, name)
        token, endpoint = get_token(), get_endpoint()

        # 1. checking the endpoint & token configuration
        if options.end_point and endpoint:
            log.warning(
                f'there are two endpoints from options<{options.end_point}> '
                f'and environment<{endpoint}>, and the first one is adopted'
            )

        if options.token and token:
            log.warning(
                f'there are two tokens from options[adopted]<{options.token}> '
                f'and environment<{token}>, and the first one is adopted'
            )

        options.end_point = options.end_point or endpoint
        options.token = options.token or token

        if not options.end_point and not options.token:
            raise WechatyPuppetConfigurationError(
                'one of <endpoint> and <token> configuration is required, '
                'you should set environment<WECHATY_PUPPET_SERVICE_TOKEN|TOKEN|token> as token,'
                'or environment<WECHATY_PUPPET_SERVICE_ENDPOINT|ENDPOINT|endpoint> as endpoint'
            )

        if options.end_point and options.token:
            log.warning(f'there are endpoint<{options.end_point}> and token<{options.token}>, '
                        f'and the endpoint will be used for service ...')

        self.channel: Optional[Channel] = None
        self._puppet_stub: Optional[PuppetStub] = None

        self._event_stream: AsyncIOEventEmitter = AsyncIOEventEmitter()

        self.login_user_id: Optional[str] = None

        # state for the event-stream reconnection loop
        self._stopping: bool = False
        # created lazily, on the loop which actually runs the event stream
        self._stop_event: Optional[asyncio.Event] = None
        self._event_listen_task: Optional[asyncio.Task] = None
        # bounded fingerprint cache of the events which have been dispatched
        # already, used to suppress the events replayed when a stream is
        # re-opened after a disconnection
        self._recent_event_keys: 'OrderedDict[str, None]' = OrderedDict()
        # True while a re-opened stream is still replaying the events of the
        # interrupted stream (see `_remember_event`)
        self._replaying_prefix: bool = False

    @property
    def puppet_stub(self) -> PuppetStub:
        """
        get the current PuppetStub instance guaranteed to be not null or raises an error.
        :return:
        """
        if self._puppet_stub is None:
            raise WechatyPuppetError('puppet_stub should not be none')
        return self._puppet_stub

    async def room_list(self) -> List[str]:
        """
        get all room list
        :return:
        """
        response = await self.puppet_stub.room_list()
        if response is None:
            raise WechatyPuppetGrpcError('can"t get room_list response')
        return response.ids

    async def message_image(self, message_id: str, image_type: ImageType = 3
                            ) -> FileBox:
        """
        get message image data
        :param message_id:
        :param image_type:
        :return:
        """
        response: MessageImageResponse = await self.puppet_stub.message_image(
            id=message_id,
            type=image_type)
        file_box = FileBox.from_json(response.filebox)
        return file_box

    def on(self, event_name: str, caller: Callable[..., None]) -> None:
        """
        listen event from the wechaty
        :param event_name:
        :param caller:
        :return:
        """
        # TODO -> if the event is listened twice, how to handle this problem
        self._event_stream.on(event_name, caller)

    def listener_count(self, event_name: str) -> int:
        """
        how to get event count
        :param event_name:
        :return:
        """
        listeners = self._event_stream.listeners(event_name)
        return len(listeners)

    async def contact_list(self) -> List[str]:
        """
        get contact list
        :return:
        """
        response = await self.puppet_stub.contact_list()
        return response.ids

    async def tag_contact_delete(self, tag_id: str) -> None:
        """
        delete some tag
        :param tag_id:
        :return:
        """
        await self.puppet_stub.tag_contact_delete(id=tag_id)
        return None

    async def tag_favorite_delete(self, tag_id: str) -> None:
        """
        delete tag favorite
        :param tag_id:
        :return:
        """
        # wechaty_grpc has not implement this function
        return None

    async def tag_contact_add(self, tag_id: str, contact_id: str) -> None:
        """
        add a tag to contact
        :param tag_id:
        :param contact_id:
        :return:
        """
        await self.puppet_stub.tag_contact_add(
            id=tag_id, contact_id=contact_id)

    async def tag_favorite_add(self, tag_id: str, contact_id: str) -> None:
        """
        add a tag to favorite
        :param tag_id:
        :param contact_id:
        :return:
        """
        # wechaty_grpc has not implement this function

    async def tag_contact_remove(self, tag_id: str, contact_id: str) -> None:
        """
        remove a tag from contact
        :param tag_id:
        :param contact_id:
        :return:
        """
        await self.puppet_stub.tag_contact_remove(
            id=tag_id,
            contact_id=contact_id)

    async def tag_contact_list(self, contact_id: Optional[str] = None
                               ) -> List[str]:
        """
        get tag list from a contact
        :param contact_id:
        :return:
        """
        response = await self.puppet_stub.tag_contact_list(
            contact_id=contact_id)
        return response.ids

    async def message_send_text(self, conversation_id: str, message: str,
                                mention_ids: Optional[List[str]] = None
                                ) -> str:
        """
        send text message
        :param conversation_id:
        :param message:
        :param mention_ids:
        :return:
        """
        response = await self.puppet_stub.message_send_text(
            conversation_id=conversation_id,
            text=message, mentonal_ids=mention_ids)
        return response.id

    async def message_send_contact(self, contact_id: str,
                                   conversation_id: str) -> str:
        """
        send contact message
        :param contact_id:
        :param conversation_id:
        :return:
        """
        response = await self.puppet_stub.message_send_contact(
            conversation_id=conversation_id,
            contact_id=contact_id
        )
        return response.id

    async def message_send_file(self, conversation_id: str,
                                file: FileBox) -> str:
        """
        send file message
        :param conversation_id:
        :param file:
        :return:
        """
        response = await self.puppet_stub.message_send_file(
            conversation_id=conversation_id,
            filebox=file.to_json_str()
        )
        return response.id

    async def message_send_url(self, conversation_id: str, url: str) -> str:
        """
        send url message
        :param conversation_id:
        :param url:
        :return:
        """
        response = await self.puppet_stub.message_send_url(
            conversation_id=conversation_id,
            url_link=url
        )
        return response.id

    async def message_send_mini_program(self, conversation_id: str,
                                        mini_program: MiniProgramPayload
                                        ) -> str:
        """
        send mini_program message
        :param conversation_id:
        :param mini_program:
        :return:
        """
        response = await self.puppet_stub.message_send_mini_program(
            conversation_id=conversation_id,
            # TODO -> check mini_program key
            mini_program=json.dumps(asdict(mini_program))
        )
        return response.id

    async def message_search(self, query: Optional[MessageQueryFilter] = None
                             ) -> List[str]:
        """
        # TODO -> this function should not be here ?
        :param query:
        :return:
        """
        return []

    async def message_recall(self, message_id: str) -> bool:
        """
        recall the message
        :param message_id:
        :return:
        """
        response = await self.puppet_stub.message_recall(id=message_id)
        return response.success

    async def message_payload(self, message_id: str) -> MessagePayload:
        """
        get message payload
        :param message_id:
        :return:
        """
        response = await self.puppet_stub.message_payload(id=message_id)

        return _map_message_type(response)

    async def message_forward(self, to_id: str, message_id: str) -> None:
        """
        forward the message
        :param to_id:
        :param message_id:
        :return:
        """
        payload = await self.message_payload(message_id=message_id)
        if payload.type == MessageType.MESSAGE_TYPE_TEXT:
            if not payload.text:
                raise Exception('no text')
            await self.message_send_text(conversation_id=to_id, message=payload.text)
        elif payload.type == MessageType.MESSAGE_TYPE_URL:
            url_payload = await self.message_url(message_id=message_id)
            await self.message_send_url(
                conversation_id=to_id,
                url=json.dumps(asdict(url_payload))
            )
        elif payload.type == MessageType.MESSAGE_TYPE_MINI_PROGRAM:
            mini_program = await self.message_mini_program(message_id=message_id)
            await self.message_send_mini_program(conversation_id=to_id, mini_program=mini_program)
        elif payload.type == MessageType.MESSAGE_TYPE_EMOTICON:
            file_box = await message_emoticon(message=payload.text)
            await self.message_send_file(conversation_id=to_id, file=file_box)
        elif payload.type == MessageType.MESSAGE_TYPE_AUDIO:
            raise WechatyPuppetOperationError('Can not support audio message forward')
        # elif payload.type == MessageType.ChatHistory:
        elif payload.type == MessageType.MESSAGE_TYPE_IMAGE:
            file_box = await self.message_image(message_id=message_id, image_type=3)
            await self.message_send_file(conversation_id=to_id, file=file_box)
        else:
            file_box = await self.message_file(message_id=message_id)
            await self.message_send_file(conversation_id=to_id, file=file_box)

    async def message_file(self, message_id: str) -> FileBox:
        """
        extract file from message
        :param message_id:
        :return:
        """
        response: MessageFileResponse = await self.puppet_stub.message_file(id=message_id)
        file_box = FileBox.from_json(response.filebox)
        return file_box

    async def message_contact(self, message_id: str) -> str:
        """
        extract
        :param message_id:
        :return:
        """
        response = await self.puppet_stub.message_contact(id=message_id)
        return response.id

    async def message_url(self, message_id: str) -> UrlLinkPayload:
        """
        extract url_link payload data from response
        :param message_id:
        :return:
        """
        response = await self.puppet_stub.message_url(id=message_id)
        # parse url_link data from response
        payload_data = json.loads(response.url_link)
        return UrlLinkPayload(
            url=payload_data.get('url', ''),
            title=payload_data.get('title', ''),
            description=payload_data.get('description', ''),
            thumbnailUrl=payload_data.get('thumbnailUrl', ''),
        )

    async def message_mini_program(self, message_id: str) -> MiniProgramPayload:
        """
        extract mini_program from message
        :param message_id:
        :return:
        """
        # TODO -> need to MiniProgram
        if self.puppet_stub is None:
            raise Exception('puppet_stub should not be none')

        response = await self.puppet_stub.message_mini_program(id=message_id)
        response_dict = json.loads(response.mini_program)
        try:
            mini_program = MiniProgramPayload(**response_dict)
        except Exception as e:
            raise WechatyPuppetPayloadError(f'can"t init mini-program payload {response_dict}')\
                from e
        return mini_program

    async def contact_alias(self, contact_id: str, alias: Optional[str] = None
                            ) -> str:
        """
        get/set contact alias
        :param contact_id:
        :param alias:
        :return:
        """
        response = await self.puppet_stub.contact_alias(
            id=contact_id, alias=alias)
        if response.alias is None and alias is None:
            raise WechatyPuppetGrpcError(f'can"t get contact<{contact_id}> alias')
        return response.alias

    async def contact_payload_dirty(self, contact_id: str) -> None:
        """
        mark the contact payload dirty status, and remove it from the cache
        """
        await self.dirty_payload(PayloadType.PAYLOAD_TYPE_CONTACT, contact_id)

    async def contact_payload(self, contact_id: str) -> ContactPayload:
        """
        get contact payload
        :param contact_id:
        :return:
        """
        response = await self.puppet_stub.contact_payload(id=contact_id)
        return response

    async def contact_avatar(self, contact_id: str,
                             file_box: Optional[FileBox] = None) -> FileBox:
        """
        get/set contact avatar
        :param contact_id:
        :param file_box:
        :return:
        """
        response = await self.puppet_stub.contact_avatar(
            id=contact_id, filebox=file_box)
        return FileBox.from_json(response.filebox)

    async def contact_tag_ids(self, contact_id: str) -> List[str]:
        """
        get contact tags
        :param contact_id:
        :return:
        """
        response = await self.puppet_stub.tag_contact_list(
            contact_id=contact_id)
        return response.ids

    def self_id(self) -> str:
        """
        # TODO -> how to get self_id, now wechaty has save login_user
            contact_id
        :return:
        """
        if not self.login_user_id:
            raise WechatyPuppetOperationError('can"t call self_id() before logined')
        return self.login_user_id

    async def friendship_search(self, weixin: Optional[str] = None,
                                phone: Optional[str] = None) -> Optional[str]:
        """
        search friendship by wexin/phone
        :param weixin:
        :param phone:
        :return:
        """
        if weixin is not None:
            weixin_response = await self.puppet_stub.friendship_search_weixin(
                weixin=weixin
            )
            if weixin_response is not None:
                return weixin_response.contact_id
        if phone is not None:
            phone_response = await self.puppet_stub.friendship_search_phone(
                phone=phone
            )
            if phone is not None:
                return phone_response.contact_id
        return None

    async def friendship_add(self, contact_id: str, hello: str) -> None:
        """
        try to add friendship
        :param contact_id:
        :param hello:
        :return:
        """
        await self.puppet_stub.friendship_add(
            contact_id=contact_id,
            hello=hello
        )

    async def friendship_payload(self, friendship_id: str,
                                 payload: Optional[FriendshipPayload] = None
                                 ) -> FriendshipPayload:
        """
        get/set friendship payload
        :param friendship_id:
        :param payload:
        :return:
        """
        response = await self.puppet_stub.friendship_payload(
            id=friendship_id, payload=json.dumps(payload)
        )
        return response

    async def friendship_accept(self, friendship_id: str) -> None:
        """
        accept friendship
        :param friendship_id:
        :return:
        """
        await self.puppet_stub.friendship_accept(id=friendship_id)

    async def room_create(self, contact_ids: List[str],
                          topic: Optional[str] = None
                          ) -> str:
        """
        create room
        :param contact_ids:
        :param topic:
        :return: created room_id
        """
        response = await self.puppet_stub.room_create(
            contact_ids=contact_ids,
            topic=topic
        )
        return response.id

    async def room_search(self,
                          query: Optional[RoomQueryFilter] = None) -> List[str]:
        """
        find the room_ids
        search room
        :param query:
        :return:
        """
        room_list_response = await self.puppet_stub.room_list()
        return room_list_response.ids

    async def room_invitation_payload(self,
                                      room_invitation_id: str,
                                      payload: Optional[RoomInvitationPayload]
                                      = None) -> RoomInvitationPayload:
        """
        get room_invitation_payload
        """
        response = await self.puppet_stub.room_invitation_payload(
            id=room_invitation_id,
            payload=payload
        )
        return RoomInvitationPayload(**response.to_dict())

    async def room_invitation_accept(self, room_invitation_id: str) -> None:
        """
        accept the room invitation
        :param room_invitation_id:
        :return:
        """
        await self.puppet_stub.room_invitation_accept(id=room_invitation_id)

    async def contact_self_qr_code(self) -> str:
        """
        :return:
        """

        response = await self.puppet_stub.contact_self_qr_code()
        return response.qrcode

    async def contact_self_name(self, name: str) -> None:
        """
        set the name of the contact
        :param name:
        :return:
        """
        await self.puppet_stub.contact_self_name(name=name)

    async def contact_signature(self, signature: str) -> None:
        """

        :param signature:
        :return:
        """

    async def room_validate(self, room_id: str) -> bool:
        """

        :param room_id:
        :return:
        """

    async def room_payload_dirty(self, room_id: str) -> None:
        """
        mark the room payload dirty status, and remove it from the cache
        :param room_id:
        :return:
        """
        await self.dirty_payload(
            PayloadType.PAYLOAD_TYPE_ROOM,
            room_id
        )

    async def room_member_payload_dirty(self, room_id: str) -> None:
        """
        mark the room-member payload dirty status, and remove it from the cache
        :param room_id:
        :return:
        """
        await self.dirty_payload(
            PayloadType.PAYLOAD_TYPE_ROOM_MEMBER,
            room_id
        )

    async def room_payload(self, room_id: str) -> RoomPayload:
        """

        :param room_id:
        :return:
        """
        response = await self.puppet_stub.room_payload(id=room_id)
        return response

    async def room_members(self, room_id: str) -> List[str]:
        """

        :param room_id:
        :return:
        """
        response = await self.puppet_stub.room_member_list(id=room_id)
        return response.member_ids

    async def room_add(self, room_id: str, contact_id: str) -> None:
        """
        add contact to room
        :param room_id:
        :param contact_id:
        :return:
        """
        await self.puppet_stub.room_add(id=room_id, contact_id=contact_id)

    async def room_delete(self, room_id: str, contact_id: str) -> None:
        """
        delete contact from room
        :param room_id:
        :param contact_id:
        :return:
        """
        await self.puppet_stub.room_del(id=room_id, contact_id=contact_id)

    async def room_quit(self, room_id: str) -> None:
        """
        quit from room
        :param room_id:
        :return:
        """
        await self.puppet_stub.room_quit(id=room_id)

    async def room_topic(self, room_id: str, new_topic: str) -> None:
        """
        set/set topic of the room
        :param room_id:
        :param new_topic:
        :return:
        """
        await self.puppet_stub.room_topic(id=room_id, topic=new_topic)

    async def room_announce(self, room_id: str,
                            announcement: Optional[str] = None) -> str:
        """
        get/set announce
        :param room_id:
        :param announcement:
        :return:
        """
        room_announce_response = await self.puppet_stub.room_announce(
            id=room_id, text=announcement)
        if announcement is None and room_announce_response.text is not None:
            # get the announcement
            return room_announce_response.text
        if announcement is not None and room_announce_response.text is None:
            return announcement
        return ''

    async def room_qr_code(self, room_id: str) -> str:
        """
        get room qr_code
        :param room_id:
        :return:
        """
        # TODO -> wechaty-grpc packages has leave out id params
        log.warning('room_qr_code() <room_id: %s> param is missing', room_id)
        room_qr_code_response = await \
            self.puppet_stub.room_qr_code()
        return room_qr_code_response.qrcode

    async def room_member_payload(self, room_id: str,
                                  contact_id: str) -> RoomMemberPayload:
        """
        get room member payload
        :param room_id:
        :param contact_id:
        :return:
        """
        member_payload = await self.puppet_stub.room_member_payload(
            id=room_id, member_id=contact_id)
        return member_payload

    async def room_avatar(self, room_id: str) -> FileBox:
        """
        get room avatar
        :param room_id:
        :return:
        """
        room_avatar_response = await self.puppet_stub.room_avatar(id=room_id)

        file_box_data = json.loads(room_avatar_response.filebox)

        if 'remoteUrl' not in file_box_data:
            raise WechatyPuppetPayloadError('invalid room avatar response')

        file_box = FileBox.from_url(
            url=file_box_data['remoteUrl'],
            name=f'avatar-{room_id}.jpeg'
        )
        return file_box

    async def dirty_payload(self, payload_type: PayloadType, payload_id: str) -> None:
        """
        mark the payload dirty status, and remove it from the cache
        """
        await self.puppet_stub.dirty_payload(
            type=payload_type.value,
            id=payload_id
        )

    def _init_puppet(self) -> None:
        """
        start puppet channel contact_self_qr_code
        """
        log.info('init puppet ...')

        # 1. if there is no endpoint, it should fetch it from chatie server with token
        if not self.options.end_point:

            url = f'https://api.chatie.io/v0/hosties/{self.options.token}'
            log.info('fetching endpoint from chatie-server: %s', url)
            response = requests.get(url)

            if response.status_code != 200:
                raise WechatyPuppetGrpcError(
                    'can"t fetch endpoint from chatie server. '
                    'You can try it later, or make sure that your pc can connect to heroku server '
                )

            data = response.json()

            if 'ip' not in data or data['ip'] == '0.0.0.0':
                raise WechatyPuppetGrpcError(
                    'Your service token has no available endpoint, is your token correct?'
                )
            if 'port' not in data:
                raise WechatyPuppetGrpcError("can't find service server port")

            self.options.end_point = f'{data["ip"]}:{data["port"]}'
            log.debug('endpoint from chatie-server : <%s>', self.options.end_point)

        if ping_endpoint(self.options.end_point) is False:
            raise WechatyPuppetConfigurationError(
                f"can't not ping endpoint: {self.options.end_point}"
            )

        host, port = extract_host_and_port(self.options.end_point)
        self.channel = Channel(host=host, port=port)

        # pylint: disable=W0212
        self.channel._authority = self.options.token

        self._puppet_stub = PuppetStub(self.channel)

    async def start(self) -> None:
        """
        start puppet_stub
        :return:
        """
        self._init_puppet()

        log.info('starting the puppet ...')

        # reset the state of the event-stream reconnection loop
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._recent_event_keys.clear()
        self._replaying_prefix = False

        try:
            await self.puppet_stub.stop()
        finally:
            await self.puppet_stub.start()

        log.info('puppet has started ...')

        # the event stream runs in its own task, so that stop() can interrupt
        # it (including a pending reconnection delay) without cancelling the
        # task which called start()
        listen_task = asyncio.ensure_future(self._listen_for_event())
        self._event_listen_task = listen_task
        try:
            # `shield` keeps the listener alive if the caller of start() is
            # cancelled; stop() cancels the listener explicitly
            await asyncio.shield(listen_task)
        except asyncio.CancelledError:
            if self._stopping:
                # stop() cancelled the listener: this is a normal stop of the
                # puppet, not a cancellation of start()
                if not listen_task.done():
                    listen_task.cancel()
                try:
                    await listen_task
                except asyncio.CancelledError:
                    pass
                return None
            if not listen_task.done():
                listen_task.cancel()
            raise
        return None

    async def stop(self) -> None:
        """
        stop the grpc channel connection

        It also interrupts a pending reconnection wait / an in-flight event
        stream, so that no new connection is established after calling it.
        """
        log.info('stop()')

        # 1. ask the event-stream loop to stop as soon as possible
        self._stopping = True
        if self._stop_event is not None:
            self._stop_event.set()

        # 2. interrupt and wait for the event-stream loop (including a pending
        #    reconnection delay) before releasing the channel
        event_listen_task = self._event_listen_task
        self._event_listen_task = None
        if event_listen_task is not None \
                and event_listen_task is not asyncio.current_task() \
                and not event_listen_task.done():
            event_listen_task.cancel()
            done, _ = await asyncio.wait(
                {event_listen_task},
                timeout=self.EVENT_STREAM_STOP_TIMEOUT
            )
            if not done:
                log.warning(
                    'the event stream did not stop within %.1fs',
                    self.EVENT_STREAM_STOP_TIMEOUT
                )
            elif not event_listen_task.cancelled():
                # retrieve the outcome, so that a failure of the listener
                # task is not reported as "never retrieved"
                event_listen_task.exception()

        # 3. release the listener and the grpc connection
        self._event_stream.remove_all_listeners()
        if self._puppet_stub is not None:
            try:
                await self._puppet_stub.stop()
            # pylint: disable=W0703
            except Exception as exception:
                log.warning('stop() failed to stop the puppet stub: %s', exception)
            finally:
                self._puppet_stub = None
        if self.channel:
            try:
                self.channel.close()
            finally:
                self.channel = None

    async def logout(self) -> None:
        """
        logout the account
        :return:
        """
        log.info('logout()')
        if self.login_user_id is None:
            raise WechatyPuppetOperationError('logout before login?')

        try:
            await self.puppet_stub.logout()
        # pylint: disable=W0703
        except Exception as exception:
            log.error('logout() rejection %s', exception)
        finally:
            payload = EventLogoutPayload(contact_id=self.login_user_id, data='logout')
            self._event_stream.emit('logout', payload)
            self.login_user_id = None

    async def login(self, user_id: str) -> None:
        """
        login the account
        :return:
        """
        self.login_user_id = user_id
        payload = EventLoginPayload(contact_id=user_id)
        self._event_stream.emit('login', payload)

    async def ding(self, data: Optional[str] = '') -> None:
        """
        set the ding event
        :param data:
        :return:
        """
        log.debug('send ding info to service server ...')

        await self.puppet_stub.ding(data=data)

    @staticmethod
    def _is_connection_error(exception: BaseException) -> bool:
        """
        Whether the exception is a transport-level (connection) failure, i.e.
        the kind of failure a reconnection can plausibly recover from.

        Errors reported by the service server for a business reason (invalid
        token, unknown method ...) are not connection errors: reconnecting
        would only reproduce them, so they must not enter the retry loop.
        """
        # grpclib raises StreamTerminatedError when the HTTP/2 stream is reset
        # by the peer: server restart, network glitch ...
        if isinstance(exception, StreamTerminatedError):
            return True

        # Only the gRPC status codes describing an interrupted/unavailable
        # transport qualify. UNAUTHENTICATED, INVALID_ARGUMENT, UNIMPLEMENTED
        # ... are permanent failures.
        if isinstance(exception, GRPCError):
            return exception.status in (
                GrpcStatus.UNAVAILABLE,
                GrpcStatus.DEADLINE_EXCEEDED,
                GrpcStatus.CANCELLED,
                GrpcStatus.ABORTED,
                GrpcStatus.INTERNAL,
                GrpcStatus.UNKNOWN,
            )

        # low-level connection failures from asyncio/h2: ConnectionResetError,
        # ConnectionRefusedError, BrokenPipeError (all OSError subclasses),
        # EOFError/IncompleteReadError, and timeouts. `asyncio.TimeoutError`
        # is listed explicitly for Python < 3.11 where it is not an `OSError`.
        return isinstance(exception, (OSError, EOFError, asyncio.TimeoutError))

    def _remember_event(self, response: Any) -> bool:
        """
        Decide whether the event response should be dispatched to listeners.

        When a stream is re-opened after an interruption, the service server
        replays the events of the interrupted stream (or at least its last
        events) as a prefix of the new one. To never dispatch the very same
        event twice, the fingerprints (`type` + raw payload) of the events
        already dispatched are remembered: while the re-opened stream is still
        replaying that prefix, a known fingerprint is suppressed. The first
        unknown event ends the replay phase, after which every event is
        forwarded as usual - a payload repeated later in the session is a
        distinct, legitimate event.

        *heartbeat* and *dong* events are pure liveness signals without an
        identity: the server re-emits them on purpose when a stream is
        (re)opened, so they are always forwarded.
        """
        # `response.type` is a betterproto enum, which behaves like an int
        event_type = int(response.type)
        if event_type in (
                int(EventType.EVENT_TYPE_HEARTBEAT),
                int(EventType.EVENT_TYPE_DONG)):
            return True

        event_key = f'{event_type}:{response.payload}'
        if self._replaying_prefix and event_key in self._recent_event_keys:
            log.debug('suppress the replayed event <%s>', event_key)
            return False

        # the first unseen event means the replay prefix is over
        self._replaying_prefix = False

        self._recent_event_keys[event_key] = None
        while len(self._recent_event_keys) > self.EVENT_STREAM_DEDUPE_CACHE_SIZE:
            self._recent_event_keys.popitem(last=False)
        return True

    def _forget_events_of_type(self, event_type: EventType) -> None:
        """
        Drop the remembered fingerprints of one event type.

        It is used when a `logout` is dispatched: the `login` event of the
        previous session must not suppress the `login` of the next one.
        """
        prefix = f'{int(event_type)}:'
        for event_key in [
                key for key in self._recent_event_keys if key.startswith(prefix)
        ]:
            del self._recent_event_keys[event_key]

    def _dispatch_event(self, response: Any) -> None:
        """
        Convert a grpc event response into a payload and emit it on the
        existing event stream.

        The payload fields and types are unchanged compared with the original
        implementation: this method is only the extraction of the former body
        of the `async for` loop in `_listen_for_event`.
        """
        # pylint: disable=R0912,R0915
        if response is None:
            return

        payload_data: dict = json.loads(response.payload)
        if response.type == int(EventType.EVENT_TYPE_SCAN):
            log.debug('receive scan info <%s>', payload_data)
            # create qr_code
            payload = EventScanPayload(
                status=ScanStatus(payload_data['status']),
                qrcode=payload_data.get('qrcode', None),
                data=payload_data.get('data', None)
            )
            self._event_stream.emit('scan', payload)

        elif response.type == int(EventType.EVENT_TYPE_DONG):
            log.debug('receive dong info <%s>', payload_data)
            payload = EventDongPayload(**payload_data)
            self._event_stream.emit('dong', payload)

        elif response.type == int(EventType.EVENT_TYPE_MESSAGE):
            # payload = get_message_payload_from_response(response)
            log.debug('receive message info <%s>', payload_data)
            event_message_payload = EventMessagePayload(
                message_id=payload_data['messageId'])
            self._event_stream.emit('message', event_message_payload)

        elif response.type == int(EventType.EVENT_TYPE_HEARTBEAT):
            log.debug('receive heartbeat info <%s>', payload_data)
            # Huan(202005) FIXME:
            #   https://github.com/wechaty/python-wechaty-puppet/issues/6
            #   Workaround for unexpected server json payload key: timeout
            # if 'timeout' in payload_data:
            #     del payload_data['timeout']
            payload_data = {'data': payload_data['data']}
            payload = EventHeartbeatPayload(**payload_data)
            self._event_stream.emit('heartbeat', payload)

        elif response.type == int(EventType.EVENT_TYPE_ERROR):
            log.info('receive error info <%s>', payload_data)
            payload = EventErrorPayload(**payload_data)
            self._event_stream.emit('error', payload)

        elif response.type == int(EventType.EVENT_TYPE_FRIENDSHIP):
            log.debug('receive friendship info <%s>', payload_data)
            payload = EventFriendshipPayload(
                friendship_id=payload_data.get('friendshipId')
            )
            self._event_stream.emit('friendship', payload)

        elif response.type == int(EventType.EVENT_TYPE_ROOM_JOIN):
            log.debug('receive room-join info <%s>', payload_data)
            payload = EventRoomJoinPayload(
                invited_ids=payload_data.get('inviteeIdList', []),
                inviter_id=payload_data.get('inviterId'),
                room_id=payload_data.get('roomId'),
                timestamp=payload_data.get('timestamp')
            )
            self._event_stream.emit('room-join', payload)

        elif response.type == int(EventType.EVENT_TYPE_ROOM_INVITE):
            log.debug('receive room-invite info <%s>', payload_data)
            payload = EventRoomInvitePayload(
                room_invitation_id=payload_data.get(
                    'roomInvitationId', None)
            )
            self._event_stream.emit('room-invite', payload)

        elif response.type == int(EventType.EVENT_TYPE_ROOM_LEAVE):
            log.debug('receive room-leave info <%s>', payload_data)
            payload = EventRoomLeavePayload(
                removed_ids=payload_data.get('removeeIdList', []),
                remover_id=payload_data.get('removerId'),
                room_id=payload_data.get('roomId'),
                timestamp=payload_data.get('timestamp')
            )
            self._event_stream.emit('room-leave', payload)

        elif response.type == int(EventType.EVENT_TYPE_ROOM_TOPIC):
            log.debug('receive room-topic info <%s>', payload_data)
            payload = EventRoomTopicPayload(
                changer_id=payload_data.get('changerId'),
                new_topic=payload_data.get('newTopic'),
                old_topic=payload_data.get('oldTopic'),
                room_id=payload_data.get('roomId'),
                timestamp=payload_data.get('timestamp')
            )
            self._event_stream.emit('room-topic', payload)

        elif response.type == int(EventType.EVENT_TYPE_READY):
            log.debug('receive ready info <%s>', payload_data)
            payload = EventReadyPayload(**payload_data)
            self._event_stream.emit('ready', payload)

        elif response.type == int(EventType.EVENT_TYPE_LOGIN):
            log.debug('receive login info <%s>', payload_data)
            event_login_payload = EventLoginPayload(
                contact_id=payload_data['contactId'])
            self.login_user_id = payload_data.get('contactId', None)
            self._event_stream.emit('login', event_login_payload)

        elif response.type == int(EventType.EVENT_TYPE_LOGOUT):
            log.debug('receive logout info <%s>', payload_data)
            payload = EventLogoutPayload(
                contact_id=payload_data['contactId'],
                data=payload_data.get('data', None)
            )
            self.login_user_id = None
            # the next session logs in again with the very same payload:
            # forget the remembered login so that it is not suppressed then
            self._forget_events_of_type(EventType.EVENT_TYPE_LOGIN)
            self._event_stream.emit('logout', payload)

        elif response.type == int(EventType.EVENT_TYPE_UNSPECIFIED):
            pass

    def _get_stop_event(self) -> asyncio.Event:
        """
        Get (and lazily create) the event which interrupts a reconnection wait.

        It is created lazily so that it is bound to the event loop which
        actually runs the event stream.
        """
        if self._stop_event is None:
            self._stop_event = asyncio.Event()
        return self._stop_event

    async def _wait_before_reconnect(self, attempt: int) -> bool:
        """
        Wait for the (exponentially growing) delay before a reconnection.

        The wait is interrupted as soon as `stop()` is called, so that the
        reconnection loop never keeps running after the puppet is stopped.

        Returns `False` when the wait was interrupted by `stop()`, in which
        case no new connection must be created.
        """
        delay = min(
            self.EVENT_STREAM_RECONNECT_BASE_DELAY * (2 ** attempt),
            self.EVENT_STREAM_RECONNECT_MAX_DELAY
        )
        log.info(
            'the event stream will be reconnected in %.2fs (attempt %s/%s) ...',
            delay, attempt + 1, self.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS
        )

        stop_event = self._get_stop_event()
        if self._stopping or stop_event.is_set():
            return False

        try:
            # the wait is interrupted as soon as stop() sets the event
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            # the delay elapsed normally: a reconnection can be attempted
            return not self._stopping
        # woken up by stop(): no new connection must be created
        return False

    async def _consume_event_stream(self) -> bool:
        """
        Consume one event stream until it ends or fails.

        Returns `True` when the stream ended (the server closed it), `False`
        when it was interrupted by a connection error. A non-connection error
        raised by the stream is re-raised so that the caller reports it.

        An error raised while handling a *single* event (malformed payload, a
        failing listener ...) is reported through the `error` event and does
        not tear the stream down: the remaining events keep being consumed.
        """
        # a freshly opened stream may replay the events of the stream it
        # replaces: suppress that replay prefix
        self._replaying_prefix = True
        try:
            async for response in self.puppet_stub.event():
                if response is None:
                    continue
                # suppress the event replayed when a stream is re-opened
                if not self._remember_event(response):
                    continue
                try:
                    self._dispatch_event(response)
                # pylint: disable=W0703
                except Exception as exception:
                    log.exception('failed to handle the event %r', response)
                    self._emit_error(
                        'failed to handle an event: '
                        f'{exception.__class__.__name__}: {exception}'
                    )
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            # Python 3.7: CancelledError derives from Exception, so it must be
            # excluded explicitly from the retry handling below
            raise
        except Exception as exception:  # pylint: disable=W0703
            if not self._is_connection_error(exception):
                raise
            log.warning('the event stream was interrupted: %r', exception)
            return False
        return True

    async def _listen_for_event(self) -> None:
        """
        Listen for the service server events, reconnecting the stream when it
        is interrupted.

        Compared with a plain `async for`, the loop:

        - re-opens the stream after a normal end or a connection error, with
          a bounded number of attempts and a growing delay;
        - gives up as soon as `stop()` is called, and never opens a new
          connection afterwards;
        - reports an exhausted reconnection budget, or a non-connection
          error, through the existing `error` event instead of exiting
          silently.
        """
        log.info('listening the event from the puppet ...')

        # pylint: disable=W0703
        consecutive_failures = 0
        try:
            while not self._stopping:
                started_at = time.monotonic()
                try:
                    ended_cleanly = await self._consume_event_stream()
                except asyncio.CancelledError:  # pylint: disable=try-except-raise
                    raise
                except Exception as exception:
                    # a business-level failure: reconnecting would only
                    # reproduce it, so report it and stop listening
                    log.error(
                        'the event stream failed with a non-connection error: %r',
                        exception
                    )
                    self._emit_error(
                        'event stream failed: '
                        f'{exception.__class__.__name__}: {exception}'
                    )
                    return

                if self._stopping:
                    break

                # a stream which lived long enough was healthy: its end does
                # not belong to the same failure burst
                if time.monotonic() - started_at >= \
                        self.EVENT_STREAM_STABLE_CONNECTION_SECONDS:
                    consecutive_failures = 0

                if consecutive_failures >= self.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS:
                    reason = (
                        'the event stream has ended'
                        if ended_cleanly else
                        'the event stream keeps being interrupted'
                    )
                    log.error(
                        '%s, and the %s reconnection attempts are exhausted',
                        reason, self.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS
                    )
                    self._emit_error(
                        f'{reason}, reconnection attempts exhausted '
                        f'({self.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS})'
                    )
                    return

                # wait for the growing delay; stop() interrupts it immediately
                if not await self._wait_before_reconnect(consecutive_failures):
                    log.info('the event stream reconnection was interrupted by stop()')
                    return

                consecutive_failures += 1
                log.info(
                    'reconnecting the event stream (attempt %s/%s) ...',
                    consecutive_failures,
                    self.EVENT_STREAM_MAX_RECONNECT_ATTEMPTS
                )
        finally:
            if self._event_listen_task is asyncio.current_task():
                self._event_listen_task = None

    def _emit_error(self, message: str) -> None:
        """
        Report a fatal event-stream failure through the existing error event.
        """
        log.error('reporting the event stream failure: %s', message)
        try:
            self._event_stream.emit('error', EventErrorPayload(data=message))
        # pylint: disable=W0703
        except Exception as exception:
            # pyee re-raises the error when no listener is attached to the
            # `error` event: do not let that break the listening loop
            log.warning('no listener consumed the event stream error: %s', exception)
