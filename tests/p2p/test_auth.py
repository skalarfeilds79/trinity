import pytest

from eth_utils import (
    decode_hex,
)

from eth_keys import keys

from cancel_token import CancelToken

from p2p import ecies
from p2p import kademlia
from p2p.auth import (
    HandshakeInitiator,
    HandshakeResponder,
)
from p2p.auth import decode_authentication
from p2p.p2p_proto import Hello
from p2p.tools.paragon import (
    ParagonPeer,
    ParagonContext,
)
from p2p.tools.asyncio_streams import (
    get_directly_connected_streams,
)
from p2p.transport import Transport


from tests.p2p.auth_constants import (
    eip8_values,
    test_values,
)


@pytest.mark.asyncio
async def test_handshake():
    # TODO: this test should be re-written to not depend on functionality in the `ETHPeer` class.
    cancel_token = CancelToken("test_handshake")
    use_eip8 = False
    initiator_remote = kademlia.Node(
        keys.PrivateKey(test_values['receiver_private_key']).public_key,
        kademlia.Address('0.0.0.0', 0, 0))
    initiator = HandshakeInitiator(
        initiator_remote,
        keys.PrivateKey(test_values['initiator_private_key']),
        use_eip8,
        cancel_token)
    initiator.ephemeral_privkey = keys.PrivateKey(test_values['initiator_ephemeral_private_key'])

    responder_remote = kademlia.Node(
        keys.PrivateKey(test_values['initiator_private_key']).public_key,
        kademlia.Address('0.0.0.0', 0, 0))
    responder = HandshakeResponder(
        responder_remote,
        keys.PrivateKey(test_values['receiver_private_key']),
        use_eip8,
        cancel_token)
    responder.ephemeral_privkey = keys.PrivateKey(test_values['receiver_ephemeral_private_key'])

    # Check that the auth message generated by the initiator is what we expect. Notice that we
    # can't use the auth_init generated here because the non-deterministic prefix would cause the
    # derived secrets to not match the expected values.
    _auth_init = initiator.create_auth_message(test_values['initiator_nonce'])
    assert len(_auth_init) == len(test_values['auth_plaintext'])
    assert _auth_init[65:] == test_values['auth_plaintext'][65:]  # starts with non deterministic k

    # Check that encrypting and decrypting the auth_init gets us the orig msg.
    _auth_init_ciphertext = initiator.encrypt_auth_message(_auth_init)
    assert _auth_init == ecies.decrypt(_auth_init_ciphertext, responder.privkey)

    # Check that the responder correctly decodes the auth msg.
    auth_msg_ciphertext = test_values['auth_ciphertext']
    initiator_ephemeral_pubkey, initiator_nonce, _ = decode_authentication(
        auth_msg_ciphertext, responder.privkey)
    assert initiator_nonce == test_values['initiator_nonce']
    assert initiator_ephemeral_pubkey == (
        keys.PrivateKey(test_values['initiator_ephemeral_private_key']).public_key)

    # Check that the auth_ack msg generated by the responder is what we expect.
    auth_ack_msg = responder.create_auth_ack_message(test_values['receiver_nonce'])
    assert auth_ack_msg == test_values['authresp_plaintext']

    # Check that the secrets derived from ephemeral key agreements match the expected values.
    auth_ack_ciphertext = test_values['authresp_ciphertext']
    aes_secret, mac_secret, egress_mac, ingress_mac = responder.derive_secrets(
        initiator_nonce, test_values['receiver_nonce'],
        initiator_ephemeral_pubkey, auth_msg_ciphertext, auth_ack_ciphertext)
    assert aes_secret == test_values['aes_secret']
    assert mac_secret == test_values['mac_secret']
    # Test values are from initiator perspective, so they're reversed here.
    assert ingress_mac.digest() == test_values['initial_egress_MAC']
    assert egress_mac.digest() == test_values['initial_ingress_MAC']

    # Check that the initiator secrets match as well.
    responder_ephemeral_pubkey, responder_nonce = initiator.decode_auth_ack_message(
        test_values['authresp_ciphertext'])
    (initiator_aes_secret,
     initiator_mac_secret,
     initiator_egress_mac,
     initiator_ingress_mac) = initiator.derive_secrets(
         initiator_nonce, responder_nonce,
         responder_ephemeral_pubkey, auth_msg_ciphertext, auth_ack_ciphertext)
    assert initiator_aes_secret == aes_secret
    assert initiator_mac_secret == mac_secret
    assert initiator_ingress_mac.digest() == test_values['initial_ingress_MAC']
    assert initiator_egress_mac.digest() == test_values['initial_egress_MAC']

    # Finally, check that two Peers configured with the secrets generated above understand each
    # other.
    (
        (responder_reader, responder_writer),
        (initiator_reader, initiator_writer),
    ) = get_directly_connected_streams()

    initiator_transport = Transport(
        remote=initiator_remote,
        private_key=initiator.privkey,
        reader=initiator_reader,
        writer=initiator_writer,
        aes_secret=initiator_aes_secret,
        mac_secret=initiator_mac_secret,
        egress_mac=initiator_egress_mac,
        ingress_mac=initiator_ingress_mac
    )
    initiator_peer = ParagonPeer(
        transport=initiator_transport,
        context=ParagonContext(),
    )
    initiator_peer.base_protocol.send_handshake()
    responder_transport = Transport(
        remote=responder_remote,
        private_key=responder.privkey,
        reader=responder_reader,
        writer=responder_writer,
        aes_secret=aes_secret,
        mac_secret=mac_secret,
        egress_mac=egress_mac,
        ingress_mac=ingress_mac,
    )
    responder_peer = ParagonPeer(
        transport=responder_transport,
        context=ParagonContext(),
    )
    responder_peer.base_protocol.send_handshake()

    # The handshake msgs sent by each peer (above) are going to be fed directly into their remote's
    # reader, and thus the read_msg() calls will return immediately.
    responder_hello, _ = await responder_peer.read_msg()
    initiator_hello, _ = await initiator_peer.read_msg()

    assert isinstance(responder_hello, Hello)
    assert isinstance(initiator_hello, Hello)


@pytest.mark.asyncio
async def test_handshake_eip8():
    cancel_token = CancelToken("test_handshake_eip8")
    use_eip8 = True
    initiator_remote = kademlia.Node(
        keys.PrivateKey(eip8_values['receiver_private_key']).public_key,
        kademlia.Address('0.0.0.0', 0, 0))
    initiator = HandshakeInitiator(
        initiator_remote,
        keys.PrivateKey(eip8_values['initiator_private_key']),
        use_eip8,
        cancel_token)
    initiator.ephemeral_privkey = keys.PrivateKey(eip8_values['initiator_ephemeral_private_key'])

    responder_remote = kademlia.Node(
        keys.PrivateKey(eip8_values['initiator_private_key']).public_key,
        kademlia.Address('0.0.0.0', 0, 0))
    responder = HandshakeResponder(
        responder_remote,
        keys.PrivateKey(eip8_values['receiver_private_key']),
        use_eip8,
        cancel_token)
    responder.ephemeral_privkey = keys.PrivateKey(eip8_values['receiver_ephemeral_private_key'])

    auth_init_ciphertext = eip8_values['auth_init_ciphertext']

    # Check that we can decrypt/decode the EIP-8 auth init message.
    initiator_ephemeral_pubkey, initiator_nonce, _ = decode_authentication(
        auth_init_ciphertext, responder.privkey)
    assert initiator_nonce == eip8_values['initiator_nonce']
    assert initiator_ephemeral_pubkey == (
        keys.PrivateKey(eip8_values['initiator_ephemeral_private_key']).public_key)

    responder_nonce = eip8_values['receiver_nonce']
    auth_ack_ciphertext = eip8_values['auth_ack_ciphertext']
    aes_secret, mac_secret, egress_mac, ingress_mac = responder.derive_secrets(
        initiator_nonce, responder_nonce, initiator_ephemeral_pubkey, auth_init_ciphertext,
        auth_ack_ciphertext)

    # Check that the secrets derived by the responder match the expected values.
    assert aes_secret == eip8_values['expected_aes_secret']
    assert mac_secret == eip8_values['expected_mac_secret']

    # Also according to https://github.com/ethereum/EIPs/blob/master/EIPS/eip-8.md, running B's
    # ingress-mac keccak state on the string "foo" yields the following hash:
    ingress_mac_copy = ingress_mac.copy()
    ingress_mac_copy.update(b'foo')
    assert ingress_mac_copy.hexdigest() == (
        '0c7ec6340062cc46f5e9f1e3cf86f8c8c403c5a0964f5df0ebd34a75ddc86db5')

    responder_ephemeral_pubkey, responder_nonce = initiator.decode_auth_ack_message(
        auth_ack_ciphertext)
    (initiator_aes_secret,
     initiator_mac_secret,
     initiator_egress_mac,
     initiator_ingress_mac) = initiator.derive_secrets(
        initiator_nonce, responder_nonce, responder_ephemeral_pubkey, auth_init_ciphertext,
        auth_ack_ciphertext)

    # Check that the secrets derived by the initiator match the expected values.
    assert initiator_aes_secret == eip8_values['expected_aes_secret']
    assert initiator_mac_secret == eip8_values['expected_mac_secret']

    # Finally, check that two Peers configured with the secrets generated above understand each
    # other.
    (
        (responder_reader, responder_writer),
        (initiator_reader, initiator_writer),
    ) = get_directly_connected_streams()

    initiator_transport = Transport(
        remote=initiator_remote,
        private_key=initiator.privkey,
        reader=initiator_reader,
        writer=initiator_writer,
        aes_secret=initiator_aes_secret,
        mac_secret=initiator_mac_secret,
        egress_mac=initiator_egress_mac,
        ingress_mac=initiator_ingress_mac
    )
    initiator_peer = ParagonPeer(
        transport=initiator_transport,
        context=ParagonContext(),
    )
    initiator_peer.base_protocol.send_handshake()
    responder_transport = Transport(
        remote=responder_remote,
        private_key=responder.privkey,
        reader=responder_reader,
        writer=responder_writer,
        aes_secret=aes_secret,
        mac_secret=mac_secret,
        egress_mac=egress_mac,
        ingress_mac=ingress_mac,
    )
    responder_peer = ParagonPeer(
        transport=responder_transport,
        context=ParagonContext(),
    )
    responder_peer.base_protocol.send_handshake()

    # The handshake msgs sent by each peer (above) are going to be fed directly into their remote's
    # reader, and thus the read_msg() calls will return immediately.
    responder_hello, _ = await responder_peer.read_msg()
    initiator_hello, _ = await initiator_peer.read_msg()

    assert isinstance(responder_hello, Hello)
    assert isinstance(initiator_hello, Hello)


def test_eip8_hello():
    # Data taken from https://github.com/ethereum/EIPs/blob/master/EIPS/eip-8.md
    payload = decode_hex(
        "f87137916b6e6574682f76302e39312f706c616e39cdc5836574683dc6846d6f726b1682270fb840"
        "fda1cff674c90c9a197539fe3dfb53086ace64f83ed7c6eabec741f7f381cc803e52ab2cd55d5569"
        "bce4347107a310dfd5f88a010cd2ffd1005ca406f1842877c883666f6f836261720304")
    Hello(cmd_id_offset=0, snappy_support=False).decode_payload(payload)
