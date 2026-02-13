"""Example plugin: send a message after successful authentication."""

from rymc.phira.protocol.data.message import ChatMessage
from rymc.phira.protocol.packet.clientbound import ClientBoundMessagePacket


PLUGIN_INFO = {
    "name": "auth_test",
    "version": "0.0.1",
}


def setup(ctx):
    def on_auth_success(connection=None, user_info=None, handler=None, **_):
        # Send to the authenticated user's connection
        try:
            connection.send(ClientBoundMessagePacket(ChatMessage(-1, "插件测试v0.0.1")))
        except Exception:
            ctx.logger.exception("failed to send auth test message")

    # Subscribe to auth success event.
    # `ctx.on` automatically binds the handler to this plugin, so hot-reload/unload is safe.
    ctx.on("auth.success", on_auth_success)

    # optional teardown
    def teardown():
        ctx.logger.info("auth_test plugin teardown")

    return teardown

# touch 1770960804.0291264

# touch2 1770960917.9835322
