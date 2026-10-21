import socket

from app import create_app

app = create_app()


def lan_address():
    """This computer's address on the local Wi-Fi, for opening the app on a phone."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # No traffic is sent; this only asks the OS which interface it would use.
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
        except OSError:
            return None


if __name__ == "__main__":
    port = 5000
    address = lan_address()
    print(f"\n  On this computer:  http://127.0.0.1:{port}")
    if address:
        print(f"  On your phone:     http://{address}:{port}   (same Wi-Fi network)\n")
    app.run(debug=app.config["DEBUG"], host="0.0.0.0", port=port)
