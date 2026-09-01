import io

from app.core.csv_utils import read_csv_rows


def test_read_csv_rows_streams_binary_input_without_closing_it():
    source = io.BytesIO(b"\xef\xbb\xbfName, Value\r\n Alpha , 1 \r\n")

    assert list(read_csv_rows(source)) == [{"Name": "Alpha", "Value": "1"}]
    assert source.closed is False


def test_read_csv_rows_accepts_text_input_without_closing_it():
    source = io.StringIO("Name,Value\nBeta,2\n")

    assert list(read_csv_rows(source)) == [{"Name": "Beta", "Value": "2"}]
    assert source.closed is False
