import json
import shutil
import statistics
from decimal import Decimal as D

import npyscreen
import simplejson

UNITS = {
    "M": 10 ** 6,
    "B": 10 ** 9,
    "T": 10 ** 12,
    "q": 10 ** 15,
    "Q": 10 ** 18,
    "s": 10 ** 21,
    "S": 10 ** 24,
    "o": 10 ** 27,
    "N": 10 ** 30,
    "d": 10 ** 33,
    "U": 10 ** 36,
    "Td": 10 ** 39,  # ???
    "Qd": 10 ** 42,  # ???
    "qd": 10 ** 45,  # ???
}


def get_unit(exp):
    return next((k for k, v in UNITS.items() if D(v).log10() == exp))


def factor_price(price):
    x = D(price)
    exp = x.adjusted()
    exp -= exp % 3
    return (x.scaleb(-exp).quantize(D("1.000"))), get_unit(exp)


def print_price(price):
    cost, unit = factor_price(price)
    return "{} {}".format(cost, unit)


class Database:
    class Price:
        def __init__(self, level, price, unit, discount_level):
            self.level = level
            self.price = price
            self.unit = unit
            self.discount_level = discount_level

        def get_price(self, at_discount_level=None):
            price = self.price * UNITS[self.unit]
            if (
                at_discount_level is None
                or at_discount_level == self.discount_level
            ):
                return price

            return (
                price
                / (1 - self.discount_level / 100)
                * (1 - at_discount_level / 100)
            )

    class Elem:
        def __init__(self, increase_type, increase_percent, last_level, prices):
            self.increase_type = increase_type
            self.increase_percent = increase_percent
            self.last_level = last_level
            self.prices = prices

        def _get_price(self, level, discount_level):
            if self.last_level is not None and level >= self.last_level:
                return None, False

            discounts = self.prices.get(level)
            if discounts is None:
                return None, False

            price = discounts.get(discount_level)
            if price is not None:
                return price.get_price(), False

            zero_discount_price = discounts.get(0)
            if zero_discount_price is not None:
                estimated_price = zero_discount_price.get_price(discount_level)
            else:
                estimated_price = statistics.mean(
                    (
                        price.get_price(discount_level)
                        for price in discounts.values()
                    )
                )

            return estimated_price, True

        def get_price_information(self, level, discount_level):
            price, is_estimate = self._get_price(level, discount_level)
            return price, is_estimate

        def get_price_info_with_payback(self, level, discount_level):
            price, is_estimate = self.get_price_information(
                level, discount_level
            )

            if price is None:
                price = self.get_new_price_estimate(level, discount_level)
                is_estimate = True

            if price is None:
                return None, None, False

            if self.increase_type == "double":
                payback_price = price * 2
            elif self.increase_type == "triple":
                payback_price = price * 3
            else:
                level_percentage = self.increase_percent * level
                multiplier = (
                    1 + self.increase_percent / 100 + level_percentage / 100
                ) / (1 + level_percentage / 100)
                payback_price = price * multiplier / (multiplier - 1)

            return price, payback_price, is_estimate

        def get_new_price_estimate(self, level, discount_level):
            def get_consecutive_pairs():
                def get_level_pairs():
                    def get_levels():
                        _level = 0
                        stop_level = self.last_level
                        if stop_level is None:
                            if not self.prices:
                                return
                            stop_level = max(self.prices.keys()) + 1
                        while _level < stop_level:
                            yield _level
                            _level += 1

                    try:
                        levels_gen = get_levels()
                        level_1 = next(levels_gen)
                        level_2 = next(levels_gen)
                        while True:
                            yield level_1, level_2
                            level_1 = level_2
                            level_2 = next(levels_gen)
                    except StopIteration:
                        pass

                try:
                    level_pairs_gen = get_level_pairs()
                    while True:
                        level_1, level_2 = next(level_pairs_gen)
                        if level_1 in self.prices and level_2 in self.prices:
                            yield level_1, level_2
                except StopIteration:
                    pass

            def calculate_multiplier(level_1, level_2):
                try:
                    discounts_1 = self.prices[level_1]
                    discounts_2 = self.prices[level_2]
                except KeyError:
                    return

                discount_set_1 = set(discounts_1)
                discount_set_2 = set(discounts_2)
                for discount in discount_set_1.intersection(discount_set_2):
                    yield discounts_2[discount].get_price() / discounts_1[
                        discount
                    ].get_price()

            def get_multipliers():
                for level_1, level_2 in get_consecutive_pairs():
                    for multiplier in calculate_multiplier(level_1, level_2):
                        yield multiplier

            def get_estimates(multiplier):
                for _level, discounts in self.prices.items():
                    level_multiplier = multiplier ** (level - _level)
                    price = discounts.get(discount_level)
                    if price is None:
                        for price in discounts.values():
                            yield price.get_price(
                                discount_level
                            ) * level_multiplier
                    else:
                        yield price.get_price(discount_level) * level_multiplier

            try:
                multiplier = statistics.mean(get_multipliers())
                estimated_price = statistics.mean(get_estimates(multiplier))
                return estimated_price
            except statistics.StatisticsError:
                return None

        def add_cost(self, level, discount_level, price, unit):
            discounts = self.prices.get(level)
            if discounts is None:
                discounts = {}
                self.prices[level] = discounts

            assert discount_level not in discounts

            discounts[discount_level] = Database.Price(
                level, price, unit, discount_level
            )

        def mark_completed(self, last_level):
            self.last_level = last_level

    def __init__(self, path):
        self._path = path
        with open(self._path) as f:
            data = json.load(f)

        self.data = {}
        for research_name, value in data.items():
            prices = {}
            for research_level, level_data in value["prices"].items():
                discounts = {}
                for discount_level, discount_data in level_data.items():
                    discounts[int(discount_level)] = Database.Price(
                        int(research_level),
                        discount_data["price"],
                        discount_data["unit"],
                        int(discount_level),
                    )
                prices[int(research_level)] = discounts

            self.data[research_name] = Database.Elem(
                value["increase_type"],
                value["increase_percent"],
                value["last_level"],
                prices,
            )

    def save(self):
        db_data = {}

        for research_name, elem in self.data.items():
            db_prices = {}
            for research_level, discounts in elem.prices.items():
                db_discounts = {}
                for discount_level, price in discounts.items():
                    db_price, db_unit = factor_price(price.get_price())
                    db_discounts[str(discount_level)] = {
                        "price": db_price,
                        "unit": db_unit,
                    }
                db_prices[research_level] = db_discounts
            db_data[research_name] = {
                "increase_type": elem.increase_type,
                "increase_percent": elem.increase_percent,
                "last_level": elem.last_level,
                "prices": db_prices,
            }

        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(simplejson.dumps(db_data, indent=4))
        shutil.move(tmp_path, self._path)


class PaybackValue:
    def __init__(self, research, cost, is_estimate, payback_value, level):
        self.research = research
        self.cost = cost
        self.is_estimate = is_estimate
        self.payback_value = payback_value
        self.level = level


class Research:
    def __init__(self, name, start_level, db_elem):
        self.name = name
        self.level = start_level
        self.db_elem = db_elem
        if self.level is None:
            self.level = 0

    def increase_level(self):
        self.level += 1

    def get_payback_values(self, discount_level, start_level=None):
        level = start_level
        if level is None:
            level = self.level
        while True:
            if (
                self.db_elem.last_level is not None
                and level >= self.db_elem.last_level
            ):
                break


            (
                cost,
                payback_value,
                is_estimate,
            ) = self.db_elem.get_price_info_with_payback(level, discount_level)

            if cost is None:
                break

            yield PaybackValue(self, cost, is_estimate, payback_value, level)

            level += 1


class State:
    def __init__(self, path, database):
        self._path = path
        with open(self._path) as f:
            data = json.load(f)

        self.discount_level = data["discount_level"]
        self.researches = []
        for key, value in data["researches"].items():
            self.researches.append(Research(key, value, database.data[key],))

        for key, elem in database.data.items():
            if next((True for r in self.researches if r.name == key), False):
                continue

            self.researches.append(Research(key, None, elem))

    def save(self):
        new_data = {
            "discount_level": self.discount_level,
            "researches": {
                research.name: research.level for research in self.researches
            },
        }

        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w") as f:
            f.write(json.dumps(new_data, indent=4))
        shutil.move(tmp_path, self._path)


def get_next_payback_values(researches, discount_level):
    values = []
    for r in researches:
        value = next(r.get_payback_values(discount_level), None)
        if value is not None:
            values.append(value)

    def sort(l):
        return sorted(
            l,
            key=lambda x: x.payback_value if x.payback_value is not None else 0,
        )

    values = sort(values)
    while values:
        v = values.pop(0)
        next_value = next(
            v.research.get_payback_values(discount_level, v.level + 1), None,
        )
        yield v
        if next_value is not None:
            values.append(next_value)
            values = sort(values)


class NextResearches(npyscreen.FormBaseNewExpanded):
    def __init__(self, state, *args, **kwargs):
        self._state = state
        self._value_generator = None
        self._grid = None
        super().__init__(args, kwargs)

    def create(self):
        self._grid = self.add(
            npyscreen.SimpleGrid,
            name="Grid",
            columns=4,
            select_whole_line=True,
            max_height=10,
        )

        self._mark_done_button = self.add(
            npyscreen.ButtonPress,
            name="MarkDone",
            when_pressed_function=self.mark_done,
        )

        self._exit_button = self.add(
            npyscreen.ButtonPress, name="Exit", when_pressed_function=self.exit
        )

    def beforeEditing(self):
        self._value_generator = get_next_payback_values(
            self._state.researches, self._state.discount_level
        )
        self._grid.values = []
        for _ in range(10):
            self._grid.values.append(
                self._get_row_data(next(self._value_generator))
            )

    def pre_edit_loop(self):
        super().pre_edit_loop()
        self.set_editing(self._mark_done_button)

    def mark_done(self):
        research = self._grid.values[0][4]
        research.increase_level()
        self._state.save()
        del self._grid.values[0]
        self._grid.values.append(
            self._get_row_data(next(self._value_generator))
        )
        self._grid.update()
        if self.parentApp.ask_for_database_updates():
            self.parentApp.switchForm("ASK_PRICE")

    def exit(self):
        self.editing = False
        self.parentApp.setNextForm(None)

    @staticmethod
    def _get_row_data(value):
        def print_cost():
            result = print_price(value.cost)
            if value.is_estimate:
                return "* " + result
            else:
                return "  " + result

        return [
            value.research.name,
            value.level,
            print_cost(),
            print_price(value.payback_value)
            if value.payback_value is not None
            else "???",
            value.research,
        ]


class QueryPriceForm(npyscreen.ActionPopup):
    def __init__(self, *args, **kwargs):
        super().__init__(args, kwargs)
        completion_text = "Reseach is completed"
        self._add_button(
            "completion_button",
            npyscreen.MiniButtonPress,
            completion_text,
            -2,
            -22 - len(completion_text),
            self._mark_research_completed,
        )

    def create(self):
        self._discount_level = None
        self._research = None
        self._estimated_cost = None
        self._estimated_unit = None
        self._mode = None

        self.add(npyscreen.FixedText, value="Add reserch cost to database")
        self._research_name_field = self.add(
            npyscreen.TitleFixedText, name="Research:", value=""
        )
        self._level_from_field = self.add(
            npyscreen.TitleFixedText, name="From level:", value=""
        )
        self.nextrely += 1
        self._cost_field = self.add(npyscreen.TitleText, name="Cost:", value="")
        self._unit_field = self.add(npyscreen.TitleText, name="Unit:", value="")

    def set_values(
        self, discount_level, research, estimated_cost, estimated_unit, mode,
    ):
        self._discount_level = discount_level
        self._research = research
        self._estimated_cost = estimated_cost
        self._estimated_unit = estimated_unit
        self._mode = mode

    def beforeEditing(self):
        self._research_name_field.value = self._research.name
        self._level_from_field.value = str(self._research.level)
        self._cost_field.value = ""
        if self._estimated_cost is not None:
            self._cost_field.value = str(self._estimated_cost)
        self._unit_field.value = ""
        if self._estimated_unit is not None:
            self._unit_field.value = self._estimated_unit
        if self._mode == "discount":
            self._added_buttons["completion_button"].hidden = True
            self._initial_widget = self._added_buttons["ok_button"]
        else:
            self._added_buttons["completion_button"].hidden = False
            self._initial_widget = self._cost_field

    def pre_edit_loop(self):
        super().pre_edit_loop()
        self.set_editing(self._initial_widget)

    def _mark_research_completed(self):
        self._research.db_elem.mark_completed(self._research.level)
        self.parentApp.database.save()
        self.editing = False

    def on_ok(self):
        try:
            price = float(self._cost_field.value)
        except ValueError:
            npyscreen.notify_confirm("Invalid cost", title="popup")
            return True

        try:
            unit = self._unit_field.value
            UNITS[unit]
        except KeyError:
            npyscreen.notify_confirm("Invalid unit", title="popup")
            return True

        self._research.db_elem.add_cost(
            self._research.level, self._discount_level, price, unit
        )
        self.parentApp.database.save()
        return False

    def on_cancel(self):
        return False

    def afterEditing(self):
        if not self.parentApp.set_next_database_update_form():
            self.parentApp.setNextFormPrevious()


class IdleAirport(npyscreen.NPSAppManaged):
    def onStart(self):
        self.database = Database("database.json")
        self.state = State("state.json", self.database)
        self.addForm("MAIN", NextResearches, self.state)
        self.addForm("ASK_PRICE", QueryPriceForm)
        self.get_next_database_update_query_data = None
        if self.ask_for_database_updates():
            self.setNextForm("ASK_PRICE")

    def ask_for_database_updates(self):
        assert self.get_next_database_update_query_data is None

        def get_researches_needing_update():
            for research_name in self.database.data:
                research = next(
                    (
                        r
                        for r in self.state.researches
                        if r.name == research_name
                    )
                )
                price, is_estimate = research.db_elem.get_price_information(
                    research.level, self.state.discount_level
                )
                if price is not None and is_estimate:
                    yield "discount", research, price

                if price is None and (
                    research.db_elem.last_level is None
                    or (
                        research.level < research.db_elem.last_level
                        and research.level not in research.db_elem.prices
                    )
                ):
                    estimated_price = research.db_elem.get_new_price_estimate(
                        research.level, self.state.discount_level
                    )
                    yield "level", research, estimated_price

        self.get_next_database_update_query_data = (
            get_researches_needing_update()
        )
        return self.set_next_database_update_form()

    def set_next_database_update_form(self):
        assert self.get_next_database_update_query_data is not None

        update_type, research, estimated_price = next(
            self.get_next_database_update_query_data, (None, None, None)
        )

        if research is None:
            self.get_next_database_update_query_data = None
            return False

        if estimated_price is None:
            estimated_cost = None
            estimated_unit = None
        else:
            estimated_cost, estimated_unit = factor_price(estimated_price)
        self.getForm("ASK_PRICE").set_values(
            self.state.discount_level,
            research,
            estimated_cost,
            estimated_unit,
            update_type,
        )
        self.getForm("ASK_PRICE").resize()
        return True


def main():
    app = IdleAirport()
    app.run()
    return


if __name__ == "__main__":
    main()
