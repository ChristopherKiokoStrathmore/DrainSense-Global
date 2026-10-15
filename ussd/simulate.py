"""Walk the USSD menus in a terminal, like a feature phone.

    python simulate.py [+254712345678]     terminal
    python simulate.py --web [port]        phone UI at http://localhost:8080/simulator

Uses DevBackend and MemoryStore unless BACKEND_BASE_URL / DB_DSN are set.
Type the option at each prompt; Ctrl-C to quit.
"""

import os
import sys
import uuid

from callback import create_app

HERE = os.path.dirname(os.path.abspath(__file__))


def web(port: int):
    """Serve the phone simulator next to the real /ussd route (dev only)."""
    from flask import send_file

    app = create_app()
    app.add_url_rule("/simulator", "simulator",
                     lambda: send_file(os.path.join(HERE, "simulator.html")))
    print(f"Phone simulator: http://localhost:{port}/simulator")
    app.run(host="127.0.0.1", port=port, debug=False)


def main():
    phone = sys.argv[1] if len(sys.argv) > 1 else "+254700000001"
    client = create_app().test_client()
    while True:
        session, text = uuid.uuid4().hex[:8], ""
        print(f"\n--- dial *384*55# from {phone} (session {session}) ---")
        while True:
            body = client.post("/ussd", data={"sessionId": session, "phoneNumber": phone,
                                              "serviceCode": "*384*55#", "text": text}).get_data(as_text=True)
            print("\n" + body[4:])
            if body.startswith("END"):
                break
            entry = input("> ").strip()
            text = f"{text}*{entry}" if text else entry
        if input("\nDial again? [Y/n] ").strip().lower() == "n":
            return


if __name__ == "__main__":
    if "--web" in sys.argv:
        rest = [a for a in sys.argv[1:] if a != "--web"]
        web(int(rest[0]) if rest else 8080)
        sys.exit()
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
