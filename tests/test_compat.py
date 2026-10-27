# -*- coding: utf-8 -*-
"""compat.membro: o nome com escopo (Qt6) primeiro, o antigo (Qt5) depois."""

import pytest

from risco_garimpo.compat import membro


class _Qt6(object):
    class StandardButton(object):
        No = 0          # valor falso: não pode cair no nome antigo
        Yes = 1


class _Qt5(object):
    Yes = 11


class _Misto(object):
    Yes = 21

    class StandardButton(object):
        Yes = 22


def test_usa_o_nome_com_escopo_quando_existe():
    assert membro(_Qt6, "StandardButton", "Yes") == 1
    assert membro(_Qt6, "StandardButton", "No") == 0
    assert membro(_Misto, "StandardButton", "Yes") == 22


def test_cai_no_nome_sem_escopo_nos_bindings_antigos():
    assert membro(_Qt5, "StandardButton", "Yes") == 11


def test_nome_inexistente_e_erro():
    with pytest.raises(AttributeError):
        membro(_Qt5, "StandardButton", "Talvez")
