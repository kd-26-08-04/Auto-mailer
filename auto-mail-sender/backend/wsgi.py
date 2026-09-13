from web_app import app

if __name__ == "__main__":
    try:
        from waitress import serve
        print("Starting server with Waitress on http://127.0.0.1:5001")
        serve(app, host="127.0.0.1", port=5001)
    except ImportError:
        app.run(host="127.0.0.1", port=5001, debug=False)
