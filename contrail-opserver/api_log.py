import argparse
import subprocess

class LogQuerier(object):

    def __init__(self):
        self._args = None

    def parse_args(self):
        parser = argparse.ArgumentParser(
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            "--start-time", help="Logs start time (format now-10m, now-1h)",
            default = "now-10m")
        parser.add_argument("--end-time", help="Logs end time",
            default = "now")
        parser.add_argument("--object-type", help="object-type")
        parser.add_argument("--identifier-name", help="identifier-name")
        parser.add_argument("--domain-name",help="domain-name")
        parser.add_argument("--tenant-name",help="tenant-name")
        self._args = parser.parse_args()
        return 0

    def validate_query(self):
        if self._args.identifier_name is not None and self._args.object_type is None:
            print("object-type is required for identifier-name")
            return None

        if self._args.tenant_name is not None and self._args.domain_name is None:
            print("domain-name is required for tenant filtering")
            return None

        if self._args.domain_name is not None and self._args.tenant_name is None:
            print("domain-name is to be used with tenant name")
            return None
        return True

    def query(self):
        start_time, end_time = self._args.start_time, self._args.end_time
        options = ""
        if self._args.object_type is not None:
            options += " --object-id " + self._args.object_type
            if self._args.identifier_name is not None:
                options += ":" + self._args.identifier_name
            else:
                options += ":*"

        if self._args.domain_name is not None:
            options += " --domain-name " + self._args.domain_name

        if self._args.tenant_name is not None:
            options += " --tenant-name " + self._args.tenant_name

        command_str = ("contrail-logs --object-type config" +
            " --start-time " + str(start_time) +
            " --end-time " + str(end_time) +
            options)
        res = subprocess.getoutput(command_str)
        print(res)

def main():
    try:
        querier = LogQuerier()
        if querier.parse_args() != 0:
            return
        if querier.validate_query() is None:
            return
        querier.query()
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
    main()
