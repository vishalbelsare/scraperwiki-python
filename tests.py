#!/usr/bin/env python
from unittest import TestCase, main
from json import loads, dumps
from subprocess import Popen, PIPE
from textwrap import dedent
import sqlite3
import os
import shutil
import datetime
import urllib2
import re

import scraperwiki

scraperwiki.sql._State.echo = True

DB_NAME = 'scraperwiki.sqlite'

class Setup(TestCase):
    def test_setup(self):
        try:
            os.remove('scraperwiki.sqlite')
        except OSError:
            pass


class TestException(TestCase):
    def testExceptionSaved(self):
        script = dedent("""
            import scraperwiki.runlog
            print scraperwiki.runlog.setup()
            raise ValueError
        """)
        process = Popen(["python", "-c", script],
                        stdout=PIPE, stderr=PIPE, stdin=open("/dev/null"))
        stdout, stderr = process.communicate()

        assert 'Traceback' in stderr, "stderr should contain the original Python traceback"
        match = re.match(r'^\w{8}-\w{4}-\w{4}-\w{4}-\w{12}', stdout)
        assert match, "runlog.setup() should return a run_id"

        l = scraperwiki.sqlite.select(
            "exception_type, run_id, time from _sw_runlog order by time desc limit 1")

        # Check that some record is stored.
        assert l
        # Check that the exception name appears.
        assert 'ValueError' in l[0][
            'exception_type'], "runlog should save exception types to the database"
        # Check that the run_id from earlier has been saved.
        assert match.group() == l[0].get(
            'run_id'), "runlog should save a run_id to the database"
        # Check that the time recorded is relatively recent.
        time_str = l[0]['time']
        then = datetime.datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')
        assert (datetime.datetime.now() - then).total_seconds() < 5 * \
            60, "run log should save a time to the database"

    def testRunlogSuccess(self):
        script = dedent("""
            import scraperwiki.runlog
            print scraperwiki.runlog.setup()
        """)
        process = Popen(["python", "-c", script],
                        stdout=PIPE, stderr=PIPE, stdin=open("/dev/null"))
        stdout, stderr = process.communicate()

        l = scraperwiki.sqlite.select(
            "time, run_id, success from _sw_runlog order by time desc limit 1")

        # Check that some record is stored.
        assert l
        # Check that it has saved a success column.
        assert l[0]['success']
        # Check that a run_id has been saved.
        match = re.match(r'^\w{8}-\w{4}-\w{4}-\w{4}-\w{12}', stdout)
        assert match.group() == l[0].get(
            'run_id'), "runlog should save a run_id to the database"
        # Check that the time is relatively recent.
        then = datetime.datetime.strptime(l[0]['time'], '%Y-%m-%d %H:%M:%S.%f')
        assert (datetime.datetime.now() - then).total_seconds() < 5 * 60

class TestSaveGetVar(TestCase):
    def savegetvar(self, var):
        scraperwiki.sqlite.save_var("weird", var)
        self.assertEqual(scraperwiki.sqlite.get_var("weird"), var)

    def test_string(self):
        self.savegetvar("asdio")

    def test_int(self):
        self.savegetvar(1)

    def test_date(self):
        date1 = datetime.datetime.now()
        date2 = datetime.date.today()
        scraperwiki.sqlite.save_var("weird", date1)
        self.assertEqual(scraperwiki.sqlite.get_var("weird"), unicode(date1))
        scraperwiki.sqlite.save_var("weird", date2)
        self.assertEqual(scraperwiki.sqlite.get_var("weird"), unicode(date2))

    def test_save_multiple_values(self):
        scraperwiki.sqlite.save_var('foo', 'hello')
        scraperwiki.sqlite.save_var('bar', 'goodbye')

        self.assertEqual('hello', scraperwiki.sqlite.get_var('foo'))
        self.assertEqual('goodbye', scraperwiki.sqlite.get_var('bar'))

class TestGetNonexistantVar(TestCase):
    def test_get(self):
        self.assertIsNone(scraperwiki.sqlite.get_var('meatball'))

class TestSaveVar(TestCase):
    def setUp(self):
        super(TestSaveVar, self).setUp()
        scraperwiki.sqlite.save_var("birthday", "November 30, 1888")
        connection = sqlite3.connect(DB_NAME)
        self.cursor = connection.cursor()

    def test_insert(self):
        self.cursor.execute("SELECT name, value_blob, type FROM `swvariables`")
        observed = self.cursor.fetchall()
        expected = [("birthday", buffer("November 30, 1888"), "text",)]
        self.assertEqual(observed, expected)

class SaveAndCheck(TestCase):
    def save_and_check(self, dataIn, tableIn, dataOut, tableOut=None, twice=True):
        if tableOut == None:
            tableOut = '[' + tableIn + ']'

        # Insert
        with scraperwiki.sqlite.Transaction():
            scraperwiki.sqlite.save([], dataIn, tableIn)

        # Observe with pysqlite
        connection = sqlite3.connect(DB_NAME)
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM %s" % tableOut)
        observed1 = cursor.fetchall()
        connection.close()

        if twice:
            # Observe with DumpTruck
            observed2 = scraperwiki.sqlite.select('* FROM %s' % tableOut)

            # Check
            expected1 = dataOut
            expected2 = [dataIn] if type(dataIn) == dict else dataIn

            self.assertListEqual(observed1, expected1)
            self.assertListEqual(observed2, expected2)

class SaveAndSelect(TestCase):
    def save_and_select(self, d):
        scraperwiki.sqlite.save([], {"foo": d})
        observed = scraperwiki.sqlite.select('* from swdata')[0]['foo']
        self.assertEqual(d, observed)


class TestUniqueKeys(SaveAndSelect):
    def test_empty(self):
        scraperwiki.sqlite.set_table(u'Chico')
        scraperwiki.sqlite.save([], {"foo": 3})
        observed = scraperwiki.sqlite.execute(u'PRAGMA index_list(Chico)')
        self.assertEqual(observed, {u'data': [], u'keys': []})

    def test_two(self):
        scraperwiki.sqlite.save(['foo', 'bar'], {'foo': 3, 'bar': 9}, u'Harpo')
        observed = scraperwiki.sqlite.execute(
            u'PRAGMA index_info(Harpo_foo_bar_unique)')

        # Indexness
        self.assertIsNotNone(observed)

        # Indexed columns
        expected = {
            u'keys': [u'seqno', u'cid', u'name'],
            u'data': [
                (0, 0, u'foo'),
                (1, 1, u'bar'),
            ]
        }
        self.assertDictEqual(observed, expected)

        # Uniqueness
        indices = scraperwiki.sqlite.execute('PRAGMA index_list(Harpo)')
        namecol = indices[u"keys"].index(u'name')
        for index in indices[u"data"]:
            if index[namecol] == u'Harpo_foo_bar_unique':
                break
        else:
            index = {}

        uniquecol = indices[u"keys"].index(u'unique')
        self.assertEqual(index[uniquecol], 1)

class TestSave(SaveAndCheck):
    def test_save_int(self):
        self.save_and_check(
            {"model-number": 293}, "model-numbers", [(293,)]
        )

    def test_save_string(self):
        self.save_and_check(
            {"lastname": "LeTourneau"}, "diesel-engineers", [
                (u'LeTourneau',)]
        )

    def test_save_twice(self):
        self.save_and_check(
            {"modelNumber": 293}, "model-numbers", [(293,)]
        )
        self.save_and_check(
            {"modelNumber": 293}, "model-numbers", [(293,), (293,)], twice=False
        )

    def test_save_true(self):
        self.save_and_check(
            {"a": True}, "a", [(1,)]
        )

    def test_save_true(self):
        self.save_and_check(
            {"a": False}, "a", [(0,)]
        )

class TestQuestionMark(TestCase):
    def test_one_question_mark_with_nonlist(self):
        scraperwiki.sqlite.execute('create table zhuozi (a text);')
        scraperwiki.sqlite.execute('insert into zhuozi values (?)', 'apple')
        observed = scraperwiki.sqlite.select('* from zhuozi')
        self.assertListEqual(observed, [{'a': 'apple'}])

    def test_one_question_mark_with_list(self):
        scraperwiki.sqlite.execute('create table zhuozi (a text);')
        scraperwiki.sqlite.execute('insert into zhuozi values (?)', ['apple'])
        observed = scraperwiki.sqlite.select('* from zhuozi')
        self.assertListEqual(observed, [{'a': 'apple'}])

    def test_multiple_question_marks(self):
        scraperwiki.sqlite.execute('create table zhuozi (a text, b text);')
        scraperwiki.sqlite.execute(
            'insert into zhuozi values (?, ?)', ['apple', 'banana'])
        observed = scraperwiki.sqlite.select('* from zhuozi')
        self.assertListEqual(observed, [{'a': 'apple', 'b': 'banana'}])


class TestDateTime(TestDb):

    def rawdate(self, table="swdata", column="datetime"):
        connection = sqlite3.connect(self.DBNAME)
        cursor = connection.cursor()
        cursor.execute("SELECT %s FROM %s LIMIT 1" % (column, table))
        rawdate = cursor.fetchall()[0][0]
        connection.close()
        return rawdate

    def test_save_date(self):
        d = datetime.datetime.strptime('1990-03-30', '%Y-%m-%d').date()
        scraperwiki.sqlite.save([], {"birthday": d})
        scraperwiki.sqlite.flush()

        self.assertEqual(str(d), self.rawdate(column="birthday"))
        self.assertEqual(
            [{u'birthday': str(d)}], scraperwiki.sqlite.select("* from swdata"))
        self.assertEqual(
            {u'keys': [u'birthday'], u'data': ([str(d)])}, scraperwiki.sqlite.execute("select * from swdata"))

    def test_save_datetime(self):
        d = datetime.datetime.strptime('1990-03-30', '%Y-%m-%d')
        scraperwiki.sqlite.save([], {"birthday": d})
        scraperwiki.sqlite.flush()

        self.assertEqual(str(d), self.rawdate(column="birthday"))
        self.assertEqual(
            [{u'birthday': str(d)}], scraperwiki.sqlite.select("* from swdata"))
        self.assertEqual(
            {u'keys': [u'birthday'], u'data': ([str(d)])}, scraperwiki.sqlite.execute("select * from swdata"))


class TestStatus(TestCase):

    'Test that the status endpoint works.'

    def test_does_nothing_if_called_outside_box(self):
        scraperwiki.status('ok')

    def test_raises_exception_with_invalid_type_field(self):
        self.assertRaises(AssertionError, scraperwiki.status, 'hello')

    # XXX neeed some mocking tests for case of run inside a box


class TestImports(TestCase):

    'Test that all module contents are imported.'

    def setUp(self):
        self.sw = __import__('scraperwiki')

    def test_import_scraperwiki_root(self):
        self.sw.scrape

    def test_import_scraperwiki_sqlite(self):
        self.sw.sqlite

    def test_import_scraperwiki_sql(self):
        self.sw.sql

    def test_import_scraperwiki_status(self):
        self.sw.status

    def test_import_scraperwiki_utils(self):
        self.sw.utils

    def test_import_scraperwiki_special_utils(self):
        self.sw.pdftoxml

if __name__ == '__main__':
    main()
