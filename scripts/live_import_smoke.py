"""Live supplier/LLM check. Read a user-provided key without echoing or saving it."""
import argparse
import getpass
from kosuzu.http import RemoteError
from kosuzu.llm import refine
from kosuzu.model import ValidationError
from kosuzu.suppliers import lookup


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--code",default="C25804")
    parser.add_argument("--model",default="deepseek-flash")
    args=parser.parse_args()
    api_key=getpass.getpass("DeepSeek API key (not saved): ")
    try:
        candidate,evidence=lookup("lcsc",args.code)
        reviewed=refine(candidate,evidence,{"api_key":api_key,"model":args.model})
        assert reviewed["mpn"]==candidate["mpn"],"Review changed MPN; inspect the live result before confirming"
        assert reviewed["source_url"]==candidate["source_url"]
        assert reviewed["image_url"]==candidate["image_url"] and reviewed["image_url"]
        assert reviewed["review"]["confirmed"] is False
        print(f"PASS live LCSC + DeepSeek: {args.code}, {reviewed['mpn']}, image, structured metadata, provenance, and confirmation gate")
        print(f"Model: {reviewed['review']['model']}; package: {reviewed['package']}; attributes: {len(reviewed['attributes'])}; warnings: {len(reviewed['review']['warnings'])}")
    except (RemoteError,ValidationError) as exc:
        print(f"Live check failed: {exc}")
        raise SystemExit(1) from None
    finally:
        api_key=""


if __name__=="__main__": main()
