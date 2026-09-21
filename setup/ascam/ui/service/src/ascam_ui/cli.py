"""CLI: `ascam-ui serve`."""
import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog='ascam-ui')
    sub = parser.add_subparsers(dest='cmd', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default=os.getenv('ASCAM_UI_HOST', '0.0.0.0'))
    serve.add_argument('--port', type=int, default=int(os.getenv('ASCAM_UI_PORT', '8000')))
    args = parser.parse_args()
    if args.cmd == 'serve':
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
