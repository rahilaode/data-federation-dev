"""CLI: `ascam-orchestrator serve`."""
import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog='ascam-orchestrator')
    sub = parser.add_subparsers(dest='cmd', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--host', default=os.getenv('ASCAM_ORCH_HOST', '0.0.0.0'))
    serve.add_argument('--port', type=int, default=int(os.getenv('ASCAM_ORCH_PORT', '8000')))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    if args.cmd == 'serve':
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
