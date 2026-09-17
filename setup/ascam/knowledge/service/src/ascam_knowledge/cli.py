"""CLI: `ascam-knowledge serve | apply-config FILE | generate-key`."""
import argparse
import logging
import os

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(prog='ascam-knowledge')
    sub = parser.add_subparsers(dest='cmd', required=True)
    serve = sub.add_parser('serve', help='menjalankan Knowledge Service')
    serve.add_argument('--host', default=os.getenv('ASCAM_KNOWLEDGE_HOST', '0.0.0.0'))
    serve.add_argument('--port', type=int, default=int(os.getenv('ASCAM_KNOWLEDGE_PORT', '8000')))
    apply = sub.add_parser('apply-config', help='menerapkan konfigurasi deklaratif')
    apply.add_argument('file')
    sub.add_parser('generate-key', help='membuat kunci enkripsi Fernet baru')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.cmd == 'serve':
        import uvicorn
        from .api.app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=False)
    elif args.cmd == 'apply-config':
        from .api.schemas import ConfigDocument
        from .db.session import make_session_factory
        from .registry_service import apply_config
        from .security.crypto import SecretBox
        with open(args.file, encoding='utf-8') as fh:
            doc = ConfigDocument.model_validate(yaml.safe_load(fh))
        box = SecretBox.from_file(os.environ['ASCAM_KNOWLEDGE_ENCRYPTION_KEYS_FILE'])
        factory = make_session_factory()
        with factory() as db, db.begin():
            summary = apply_config(db, doc, box, actor='system:cli')
        print(summary.model_dump_json(indent=2))
    elif args.cmd == 'generate-key':
        from .security.crypto import generate_key
        print(generate_key())


if __name__ == '__main__':
    main()
