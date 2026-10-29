# -*- coding: utf-8 -*-
"""Тесты интерпретатора sneg. Запуск: python -m unittest discover -s tests"""

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sneg import SnegError, run_source, to_string  # noqa: E402


def run(source, stdin=""):
    """Выполняет программу и возвращает (вывод, ошибка или None)."""
    out = io.StringIO()
    try:
        run_source(source, "<тест>", out=out, stdin=io.StringIO(stdin))
    except SnegError as err:
        return out.getvalue(), err
    return out.getvalue(), None


def out_of(source, stdin=""):
    text, err = run(source, stdin)
    if err is not None:
        raise AssertionError(f"неожиданная ошибка: {err.message}")
    return text


class TestBasics(unittest.TestCase):
    def test_arithmetic_precedence(self):
        self.assertEqual(out_of("print(2 + 3 * 4)"), "14\n")

    def test_division_and_modulo(self):
        self.assertEqual(out_of("print(7 / 2)\nprint(7 % 2)"), "3.5\n1\n")

    def test_integer_printing(self):
        self.assertEqual(out_of("print(4)\nprint(4.0)\nprint(4.5)"), "4\n4\n4.5\n")

    def test_string_concat_with_number(self):
        self.assertEqual(out_of('print("итого: " + 5)'), "итого: 5\n")

    def test_comparisons_and_logic(self):
        self.assertEqual(
            out_of("print(1 < 2 and 3 > 2)\nprint(1 == 1.0)\nprint(not false)"),
            "true\ntrue\ntrue\n")

    def test_short_circuit_returns_operand(self):
        self.assertEqual(out_of('print(nil or "запасное")'), "запасное\n")

    def test_unary_minus(self):
        self.assertEqual(out_of("print(-5 + 2)"), "-3\n")

    def test_comments_are_ignored(self):
        source = """
        # комментарий решёткой
        // и ещё один
        /* и блочный */
        print("ок")   # в конце строки
        """
        self.assertEqual(out_of(source), "ок\n")


class TestVariables(unittest.TestCase):
    def test_let_and_assign(self):
        self.assertEqual(out_of("let x = 1\nx = x + 2\nprint(x)"), "3\n")

    def test_compound_assignment(self):
        self.assertEqual(
            out_of("let x = 10\nx += 5\nx -= 3\nx *= 2\nx /= 4\nprint(x)"), "6\n")

    def test_redeclare_same_scope_is_error(self):
        _, err = run("let x = 1\nlet x = 2")
        self.assertIsNotNone(err)
        self.assertIn("уже объявлена", err.message)

    def test_undeclared_variable(self):
        _, err = run("print(нет_такой)")
        self.assertIsNotNone(err)
        self.assertIn("не объявлена", err.message)
        self.assertEqual(err.line, 1)

    def test_russian_keywords(self):
        source = '''
        пусть имя = "sneg"
        если имя == "sneg" {
            print("кириллица работает")
        } иначе {
            print("не должно случиться")
        }
        пока false {
            print("цикл не выполнится")
        }
        '''
        self.assertEqual(out_of(source), "кириллица работает\n")

    def test_unicode_identifiers(self):
        self.assertEqual(out_of("let здоровье = 100\nprint(здоровье)"), "100\n")


class TestControlFlow(unittest.TestCase):
    def test_if_elseif_else(self):
        source = """
        fn grade(score) {
            if score >= 90 { return "5" }
            elseif score >= 70 { return "4" }
            else { return "2" }
        }
        print(grade(95), grade(80), grade(10))
        """
        self.assertEqual(out_of(source), "5 4 2\n")

    def test_while_and_break(self):
        source = """
        let i = 0
        while true {
            i += 1
            if i >= 3 { break }
        }
        print(i)
        """
        self.assertEqual(out_of(source), "3\n")

    def test_continue(self):
        source = """
        let sum = 0
        for i in range(0, 6) {
            if i % 2 == 0 { continue }
            sum += i
        }
        print(sum)
        """
        self.assertEqual(out_of(source), "9\n")

    def test_for_over_list_map_string(self):
        source = """
        for x in [1, 2] { print(x) }
        for k in { a: 1, b: 2 } { print(k) }
        for ch in "ок" { print(ch) }
        """
        self.assertEqual(out_of(source), "1\n2\na\nb\nо\nк\n")

    def test_nested_loops_and_break(self):
        source = """
        let found = 0
        for i in range(0, 3) {
            for j in range(0, 3) {
                if j == 1 { break }
                found += 1
            }
        }
        print(found)
        """
        self.assertEqual(out_of(source), "3\n")

    def test_break_outside_loop(self):
        _, err = run("break")
        self.assertIsNotNone(err)
        self.assertIn("внутри цикла", err.message)


class TestFunctions(unittest.TestCase):
    def test_factorial_recursion(self):
        source = """
        fn fact(n) {
            if n <= 1 { return 1 }
            return n * fact(n - 1)
        }
        print(fact(5))
        """
        self.assertEqual(out_of(source), "120\n")

    def test_closure_counter(self):
        source = """
        fn make_counter() {
            let n = 0
            return fn() {
                n += 1
                return n
            }
        }
        let c = make_counter()
        print(c(), c(), c())
        """
        self.assertEqual(out_of(source), "1 2 3\n")

    def test_function_without_return_gives_nil(self):
        self.assertEqual(out_of("fn f() { let x = 1 }\nprint(f())"), "nil\n")

    def test_wrong_arity_message(self):
        _, err = run("fn f(a, b) { return a }\nf(1)")
        self.assertIsNotNone(err)
        self.assertIn("ждёт 2 аргумент", err.message)

    def test_error_inside_function_shows_frame(self):
        _, err = run("fn boom() {\n  return 1 / 0\n}\nboom()")
        self.assertIsNotNone(err)
        self.assertIn("деление на ноль", err.message)
        self.assertTrue(any("boom" in f for f in err.frames))

    def test_return_outside_function(self):
        _, err = run("return 5")
        self.assertIsNotNone(err)
        self.assertIn("внутри функции", err.message)


class TestCollections(unittest.TestCase):
    def test_list_operations(self):
        source = """
        let a = [3, 1, 2]
        push(a, 4)
        print(len(a), a)
        sort(a)
        print(a)
        print(pop(a), a)
        """
        self.assertEqual(out_of(source), "4 [3, 1, 2, 4]\n[1, 2, 3, 4]\n4 [1, 2, 3]\n")

    def test_indexing_and_assignment(self):
        source = """
        let a = [1, 2, 3]
        a[0] = 10
        print(a[0], a[1])
        """
        self.assertEqual(out_of(source), "10 2\n")

    def test_index_out_of_range(self):
        _, err = run("let a = [1]\nprint(a[5])")
        self.assertIsNotNone(err)
        self.assertIn("вне списка", err.message)

    def test_map_fields(self):
        source = """
        let p = { name: "sneg", hp: 100 }
        p.hp -= 30
        print(p.name, p.hp)
        print(p.missing)
        print(has(p, "hp"), has(p, "mana"))
        delete(p, "hp")
        print(keys(p))
        """
        self.assertEqual(out_of(source), "sneg 70\nnil\ntrue false\n[\"name\"]\n")

    def test_in_operator(self):
        source = """
        print(2 in [1, 2, 3])
        print("a" in { a: 1 })
        print("не" in "нет")
        """
        self.assertEqual(out_of(source), "true\ntrue\ntrue\n")

    def test_nested_structures(self):
        source = """
        let data = { items: [ { id: 1 }, { id: 2 } ] }
        print(data.items[1].id)
        """
        self.assertEqual(out_of(source), "2\n")

    def test_list_length_property(self):
        self.assertEqual(out_of("let a = [1, 2]\nprint(a.length)"), "2\n")


class TestBuiltins(unittest.TestCase):
    def test_string_helpers(self):
        source = """
        print(upper("sneg"), lower("SNEG"))
        print(join(["a", "b"], "-"))
        print(split("a,b,c", ","))
        """
        self.assertEqual(out_of(source), "SNEG sneg\na-b\n[\"a\", \"b\", \"c\"]\n")

    def test_num_parsing(self):
        self.assertEqual(out_of('print(num("42"), num("3,5"), num("abc"))'),
                         "42 3.5 nil\n")

    def test_math(self):
        source = "print(abs(-3), floor(2.9), ceil(2.1), round(2.5), sqrt(9))"
        self.assertEqual(out_of(source), "3 2 3 3 3\n")

    def test_min_max(self):
        self.assertEqual(out_of("print(min([3, 1, 2]), max(4, 9, 2))"), "1 9\n")

    def test_type_of_values(self):
        source = 'print(type(nil), type(1), type("a"), type([]), type({}), type(print))'
        self.assertEqual(out_of(source),
                         "nil number string list map function\n")

    def test_input_builtin(self):
        self.assertEqual(out_of('let name = input()\nprint("привет, " + name)', "sneg\n"),
                         "привет, sneg\n")

    def test_assert_and_error(self):
        _, err = run('assert(1 == 2, "числа не равны")')
        self.assertIsNotNone(err)
        self.assertEqual(err.message, "числа не равны")

    def test_range_values(self):
        self.assertEqual(out_of("let r = range(1, 10, 3)\nfor x in r { print(x) }"),
                         "1\n4\n7\n")


class TestErrors(unittest.TestCase):
    def test_division_by_zero(self):
        _, err = run("print(1 / 0)")
        self.assertIn("деление на ноль", err.message)

    def test_type_error_message(self):
        _, err = run('print(1 - "a")')
        self.assertIn("не работает с number и string", err.message)

    def test_call_not_a_function(self):
        _, err = run("let x = 5\nx()")
        self.assertIn("не функция", err.message)

    def test_caret_position(self):
        _, err = run("let x = 1 +\n")
        self.assertIsNotNone(err)

    def test_unclosed_block(self):
        _, err = run("if true {\n print(1)\n")
        self.assertIn("не хватает }", err.message)

    def test_unclosed_string(self):
        _, err = run('print("abc')
        self.assertIn("незакрытая строка", err.message)

    def test_field_of_nil(self):
        _, err = run("let x = nil\nprint(x.field)")
        self.assertIn("у nil нет поля", err.message)


class TestFormatting(unittest.TestCase):
    def test_to_string_of_structures(self):
        self.assertEqual(to_string([1, "a", True, None]), '[1, "a", true, nil]')
        self.assertEqual(to_string({"k": [1, 2]}), '{k: [1, 2]}')

    def test_float_precision(self):
        self.assertEqual(out_of("print(0.1 + 0.2)"), "0.30000000000000004\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
