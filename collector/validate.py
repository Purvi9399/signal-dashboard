import argparse
import re
import unittest


def has_tags_block(terraform_text):
    """Return True when a Terraform file contains a tags block and description field."""
    has_tags = bool(re.search(r"\btags\s*\{", terraform_text, re.MULTILINE))
    has_description = bool(re.search(r"\bdescription\s*=\s*\".*?\"", terraform_text, re.MULTILINE))
    return has_tags and has_description


class TestTerraformValidation(unittest.TestCase):
    def test_has_tags_block(self):
        sample = '''
resource "aws_instance" "example" {
  ami           = "ami-123456"
  instance_type = "t3.micro"
  tags = {
    Name = "example"
  }
  description = "example instance"
}
'''
        self.assertTrue(has_tags_block(sample))


def main():
    parser = argparse.ArgumentParser(description="Validate Terraform tags block")
    parser.add_argument("path", nargs="?", help="Path to a Terraform file")
    args = parser.parse_args()

    if args.path is None:
        parser.error("the following arguments are required: path")

    with open(args.path, "r", encoding="utf-8") as handle:
        contents = handle.read()

    print("VALID" if has_tags_block(contents) else "INVALID")


if __name__ == "__main__":
    main()
