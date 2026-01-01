from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .models import ROLES, STATUT_ABSENCE, Absence, Annee, Mois, TypeAbsence, JourFerie, Profile, ValidationHistorique,  QuotaAbsence, TypeAbsence, Annee
from django.contrib import messages
from datetime import datetime
from django.shortcuts import render
from datetime import timedelta
from .utils import compter_jours_ouvres
from django.contrib.auth.models import User
from django.utils import timezone
from django.http import JsonResponse
from django.http import FileResponse, Http404
import os
import json
from django.core.paginator import Paginator
from azure.storage.blob import BlobServiceClient
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db.models import Prefetch
from django.utils.html import escape
from datetime import date
from django.db.models import Q
from django.db.models.functions import TruncMonth
from django.db.models import Count
from django.contrib.auth.decorators import login_required, user_passes_test
from calendar import month_name
from django.db.models.functions import ExtractMonth
from collections import OrderedDict
from django.core.files.storage import default_storage
import mimetypes
from decimal import Decimal, InvalidOperation
from django.shortcuts import render
from django.urls import reverse
from .models import Recuperation
from django.http import HttpResponse
import csv
import math




# -----------------------------
# Accueil public
# -----------------------------
def accueil_public(request):
    # Liste des mois
    mois_noms = [
        "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]

    # Utilisateurs actifs uniquement
    utilisateurs = User.objects.filter(
        profile__actif=True
    ).filter(
        Q(absences__statut__in=["verifie_drh", "valider", "approuve_superieur", "valide_dp"]) | Q(recuperation__isnull=False)
    ).distinct().order_by("last_name")

    lignes = []

    for user in utilisateurs:
        # Absences encore valides (non terminées)
        absences = Absence.objects.filter(
            collaborateur=user,
            statut__in=["verifie_drh","valider","approuve_superieur", "valide_dp"],
            date_fin__gte=date.today()  # exclut les absences expirées
        ).order_by("date_debut")

        # Récupérations encore valides (non expirées)
        recups = Recuperation.objects.filter(utilisateur=user).order_by("date_debut")

        absences_par_mois = [[] for _ in range(12)]
        total_absences = Decimal(0)

        # ➕ Ajouter les absences
        for absence in absences:
            absence.obj_type = "Absence"
            mois = absence.date_debut.month - 1
            absences_par_mois[mois].append(absence)
            total_absences += absence.duree()

        # ➕ Ajouter les récupérations (encore valides uniquement)
        for recup in recups:
            recup.obj_type = "Recuperation"
            try:
                recup.date_fin = recup.date_debut + timedelta(days=float(recup.nombre_jours) - 1)
            except Exception:
                recup.date_fin = recup.date_debut

            if recup.date_fin >= date.today():  # garde seulement les récupérations encore valides
                mois = recup.date_debut.month - 1
                absences_par_mois[mois].append(recup)

        # ⚠️ On ajoute le collaborateur seulement s’il a encore au moins une absence/récupération valide
        if any(absences_par_mois):
            lignes.append({
                "user": user,
                "mois": absences_par_mois,
                "total": total_absences,
            })

    return render(request, "accueil.html", {
        "mois_noms": mois_noms,
        "lignes": lignes,
    })
# -----------------------------
# Login Avec des profiles
# -----------------------------
def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            try:
                profil = Profile.objects.get(user=user)
                role = profil.role
                if profil.doit_changer_mdp:
                    return redirect('changer_mot_de_passe')
                if role == "collaborateur":
                    return redirect('dashboard_collaborateur')
                elif role == "superieur":
                    return redirect('dashboard_superieur')
                elif role == "drh":
                    return redirect('dashboard_drh')
                elif role == "dp":
                    return redirect('dashboard_dp')
                elif role == "admin":
                    return redirect('admin_users')  # vers l'interface Django Admin
                else:
                    messages.error(request, "Rôle inconnu. Contactez l'administrateur.")
            except Profile.DoesNotExist:
                messages.error(request, "Profil introuvable. Contactez l'administrateur.")
        else:
            messages.error(request, 'Identifiants incorrects.')

    return render(request, 'auth/login.html')

# -----------------------------
# Changer mot de passe
# -----------------------------
@login_required
def changer_mot_de_passe(request):
    if request.method == 'POST':
        nouveau_mdp = request.POST.get('nouveau_mdp')
        confirm = request.POST.get('confirm_mdp')
        if nouveau_mdp == confirm:
            request.user.set_password(nouveau_mdp)
            request.user.save()
            request.user.profile.doit_changer_mdp = False
            request.user.profile.save()
            messages.success(request, "Mot de passe changé avec succès. Connectez-vous à nouveau.")
            return redirect('login')
        else:
            messages.error(request, "Les mots de passe ne correspondent pas.")

    return render(request, 'auth/changer_mdp.html')


# -----------------------------
# Deconnexion
# -----------------------------
def logout_view(request):
    logout(request)
    return redirect('accueil_public')

# -----------------------------
# Dashboard pour les supérieurs
# -----------------------------

@login_required
def dashboard_superieur(request):
    profil = Profile.objects.get(user=request.user)
    collaborateurs = Profile.objects.filter(superieur=request.user, role='collaborateur').values_list('user', flat=True)

    absences_en_attente = Absence.objects.filter(
        collaborateur__in=collaborateurs,
        statut='verifie_drh'
    ).select_related('collaborateur', 'type_absence').prefetch_related('historiques')

    absences_approuvees = Absence.objects.filter(
        collaborateur__in=collaborateurs,
        statut='valide_dp'
    ).select_related('collaborateur', 'type_absence')

    if request.method == 'POST':
        absence_id = request.POST.get('absence_id')
        decision = request.POST.get('decision')
        motif = request.POST.get('motif', '').strip()

        try:
            absence = Absence.objects.get(id=absence_id)
            if decision == 'valider':
                absence.statut = 'approuve_superieur'
            elif decision == 'rejeter':
                absence.statut = 'rejete'
                absence.motif_rejet = motif
            absence.save()
            ValidationHistorique.objects.create(
                absence=absence,
                utilisateur=request.user,
                role_valide='superieur',
                decision=decision,
                motif=motif if decision == 'rejeter' else ''
            )
            messages.success(request, f"Demande {decision} avec succès.")
            return redirect('dashboard_superieur')
        except Absence.DoesNotExist:
            messages.error(request, "Demande introuvable.")

    context = {
        'absences': absences_en_attente,
        'absences_approuvees': absences_approuvees
    }
    return render(request, 'dashboard/superieurs.html', context)

# -----------------------------
# Dashboard pour les collaborateurs
# -----------------------------
@login_required
def dashboard_collaborateur(request):
    return render(request, 'dashboard/collaborateurs.html')


# -----------------------------
# VERIFIER QUOTA
# ----------------------------- 
def verifier_quota(user, type_absence, nombre_jours_demande, annee=None):
    """
    Vérifie si l'utilisateur a assez de quota pour le type d'absence donné et l'année spécifiée.
    Retourne True si suffisant, False sinon.
    """
    if annee is None:
        from datetime import date
        annee = date.today().year

    try:
        quota = QuotaAbsence.objects.get(user=user, type_absence=type_absence, annee=annee)
    except QuotaAbsence.DoesNotExist:
        # Aucun quota défini = refus
        return False

    # On vérifie le quota disponible
    return quota.jours_disponibles >= nombre_jours_demande

# -----------------------------
# Soumettre une absence
# ----------------------------- 
@login_required
def soumettre_absence(request, absence_id=None):
    types_absence = TypeAbsence.objects.all()
    jours_feries_qs = JourFerie.objects.all()
    jours_feries = [j.date.strftime('%Y-%m-%d') for j in jours_feries_qs]

    absence = None
    if absence_id:
        absence = get_object_or_404(Absence, id=absence_id, collaborateur=request.user)

    # Pré-remplissage pour modification
    form_data = {
        'type_absence': absence.type_absence.id if absence else '',
        'date_debut': absence.date_debut.strftime('%Y-%m-%d') if absence else '',
        'nombre_jours': absence.nombre_jours if absence else '',
        'raison': absence.raison if absence else '',
    }

    if request.method == 'POST':
        type_id = request.POST.get('type_absence')
        date_debut = request.POST.get('date_debut')
        nombre_jours = request.POST.get('nombre_jours')
        raison = request.POST.get('raison')
        justificatif = request.FILES.get('justificatif')

        # Met à jour form_data pour réaffichage en cas d'erreur
        form_data.update({
            'type_absence': type_id,
            'date_debut': date_debut,
            'nombre_jours': nombre_jours,
            'raison': raison,
        })

        # Vérification des champs obligatoires
        if not type_id or not date_debut or not nombre_jours:
            messages.error(request, "Tous les champs obligatoires doivent être remplis.")
            return render(request, 'collaborateur/soumettre_absence.html', {
                'types_absence': types_absence,
                'jours_feries': jours_feries,
                'absence': absence,
                'form_data': form_data,
            })

        try:
            type_absence = TypeAbsence.objects.get(pk=type_id)
            date_debut_obj = datetime.strptime(date_debut, "%Y-%m-%d").date()
            nombre_jours_float = float(nombre_jours)
            if nombre_jours_float <= 0:
                raise ValueError
        except (TypeAbsence.DoesNotExist, ValueError):
            messages.error(request, "Données invalides dans le formulaire.")
            return render(request, 'collaborateur/soumettre_absence.html', {
                'types_absence': types_absence,
                'jours_feries': jours_feries,
                'absence': absence,
                'form_data': form_data,
            })

        # Vérification du quota (en plus du clean)
        annee_demande = date_debut_obj.year
        if not verifier_quota(request.user, type_absence, nombre_jours_float, annee_demande):
            messages.error(request, f"Quota insuffisant pour ce type d'absence pour l'année {annee_demande}.")
            return render(request, 'collaborateur/soumettre_absence.html', {
                'types_absence': types_absence,
                'jours_feries': jours_feries,
                'absence': absence,
                'form_data': form_data,
            })

        # Création ou modification de l'absence
        if absence:
            # --- Modification ---
            absence.type_absence = type_absence
            absence.date_debut = date_debut_obj
            absence.nombre_jours = nombre_jours_float
            absence.raison = raison
            absence.statut = 'en_attente'  # repasse à l’état initial
            if justificatif:
                absence.justificatif = justificatif
            # Vérification de chevauchement                 
            jours_calendaires = math.ceil(nombre_jours_float)
            date_fin_tmp = date_debut_obj + timedelta(days=jours_calendaires - 1)
            chevauchement = Absence.objects.filter(
                collaborateur=request.user,
                date_debut__lte=date_fin_tmp,
                date_fin__gte=date_debut_obj,
                statut__in=['en_attente', 'approuve_superieur', 'verifie_drh', 'valide_dp']
            )

            if absence:
                chevauchement = chevauchement.exclude(id=absence.id)

            if chevauchement.exists():
                messages.error(
                    request,
                    "Une autre absence (active) chevauche déjà cette période."
                )
                return render(request, 'collaborateur/soumettre_absence.html', {
                    'types_absence': types_absence,
                    'jours_feries': jours_feries,
                    'absence': absence,
                    'form_data': form_data,
                })

            
            absence.save()

            ValidationHistorique.objects.create(
                absence=absence,
                utilisateur=request.user,
                decision='modifiee_par_collaborateur',
                motif="Demande modifiée par le collaborateur"
            )
            messages.success(request, "Demande d'absence modifiée avec succès.")

        else:
            # --- Création ---
            absence = Absence(
                collaborateur=request.user,
                type_absence=type_absence,
                date_debut=date_debut_obj,
                nombre_jours=nombre_jours_float,
                raison=raison,
                justificatif=justificatif,
                statut='en_attente'
            )
         
            absence.save()
            messages.success(request, "Demande d’absence soumise avec succès.")

        return redirect('mes_absences')

    # GET : affichage formulaire
    return render(request, 'collaborateur/soumettre_absence.html', {
        'types_absence': types_absence,
        'jours_feries': jours_feries,
        'absence': absence,
        'form_data': form_data,
    })

  
# -----------------------------
# Annuler une absence
# -----------------------------
    
@login_required
def annuler_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id, collaborateur=request.user)

    if absence.statut not in ['en_attente', 'approuve_superieur', 'verifie_drh', 'valide_dp']:
        messages.error(request, "Cette absence ne peut pas être annulée.")
        return redirect('mes_absences')

    if request.method == "POST":
        motif = request.POST.get('motif_annulation')
        if not motif:
            messages.error(request, "Veuillez fournir un motif d'annulation.")
            return redirect('mes_absences')

        absence.statut = 'annulee'
        absence.annulee_par_collaborateur = True
        absence.motif_annulation = motif
        absence.save()

        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            decision='annulee',
            motif=motif
        )

        messages.success(request, "Votre demande a été annulée avec succès.")
        return redirect('mes_absences')

    return render(request, 'collaborateur/annuler_absence.html', {'absence': absence})

    
    

# -----------------------------
# quota d'absence
# -----------------------------
    
@login_required
def modifier_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id, collaborateur=request.user)

    if absence.statut not in ['en_attente', 'rejete']:
        messages.error(request, "Vous ne pouvez modifier qu'une demande en attente ou rejetée.")
        return redirect('mes_absences')

    if request.method == "POST":
        type_id = request.POST.get('type_absence')
        date_debut = request.POST.get('date_debut')
        nombre_jours = request.POST.get('nombre_jours')
        raison = request.POST.get('raison')
        justificatif = request.FILES.get('justificatif')

        try:
            type_absence = TypeAbsence.objects.get(pk=type_id)
            date_debut_obj = datetime.strptime(date_debut, "%Y-%m-%d").date()
            nombre_jours_float = float(nombre_jours)
        except Exception:
            messages.error(request, "Données invalides.")
            return redirect('modifier_absence', absence_id=absence.id)

        # Mise à jour
        absence.type_absence = type_absence
        absence.date_debut = date_debut_obj
        absence.nombre_jours = nombre_jours_float
        absence.raison = raison
        if justificatif:
            absence.justificatif = justificatif

        absence.statut = 'en_attente'
        absence.approuve_par_superieur = False
        absence.verifie_par_drh = False
        absence.valide_par_dp = False
        absence.save()

        # Historique
        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            decision='modifiee_par_collaborateur',
            motif="Demande modifiée par le collaborateur"
        )

        messages.success(request, "Votre demande a été modifiée et renvoyée en validation.")
        return redirect('mes_absences')

    return render(request, 'collaborateur/modifier_absence.html', {
        'absence': absence,
        'types_absence': TypeAbsence.objects.all(),
    })


# -----------------------------
# quota d'absence
# -----------------------------
@login_required
def mon_quota(request):
    quotas = request.user.quotas.all().order_by('type_absence__nom', 'annee')
    return render(request, 'collaborateur/mon_quota.html', {'quotas': quotas})


# -----------------------------
# liste des absences du collaborateur
# -----------------------------
from datetime import timedelta

from datetime import timedelta
from django.db.models import Prefetch

@login_required
def mes_absences(request):
    # 1️⃣ Récupérer toutes les absences
    absences = Absence.objects.filter(collaborateur=request.user).order_by('-date_creation').prefetch_related(
        Prefetch('historiques', queryset=ValidationHistorique.objects.order_by('-date_validation'))
    )

    # 2️⃣ Récupérer toutes les récupérations
    recuperations = Recuperation.objects.filter(utilisateur=request.user).order_by('-date_soumission')

    # 3️⃣ Préparer les récupérations pour le template
    for r in recuperations:
        r.type_demande = 'Recuperation'
        if not hasattr(r, 'statut') or r.statut is None:
            r.statut = 'en_attente'
        # Gestion de la date de fin
        if float(r.nombre_jours) <= 1:
            r.date_fin = r.date_debut
        else:
            r.date_fin = r.date_debut + timedelta(days=float(r.nombre_jours) - 1)

    # 4️⃣ Préparer les absences pour le template
    for a in absences:
        a.type_demande = 'Absence'
        if not hasattr(a, 'date_fin') or a.date_fin is None:
            if float(a.nombre_jours) <= 1:
                a.date_fin = a.date_debut
            else:
                a.date_fin = a.date_debut + timedelta(days=float(a.nombre_jours) - 1)

    # 5️⃣ Fusionner et trier par date de début décroissante
    demandes = sorted(list(absences) + list(recuperations), key=lambda x: x.date_debut, reverse=True)

    # 6️⃣ Types d'absence et jours fériés
    types_absence = TypeAbsence.objects.all()
    jours_feries_qs = JourFerie.objects.all()
    jours_feries = [j.date.strftime('%Y-%m-%d') for j in jours_feries_qs]

    # 7️⃣ Statuts modifiables pour les absences
    statuts_modifiables = [s[0] for s in STATUT_ABSENCE if s[0] in ('en_attente', 'valider', 'approuve_superieur', 'verifie_drh')]

    return render(request, 'collaborateur/mes_absences.html', {
        'absences': demandes,
        'types_absence': types_absence,
        'jours_feries': jours_feries,
        'statuts_modifiables': statuts_modifiables,
    })
# -----------------------------
# calendrier des absences
# -----------------------------
@login_required
def calendrier_absences(request):
    absences = Absence.objects.filter(statut='valide_dp')
    recuperation = Recuperation.objects.filter(statut='valide')
    types = TypeAbsence.objects.all()
    utilisateurs = User.objects.all()

    events = []
    for a in absences:
        events.append({
            "title": f"{a.collaborateur.get_full_name()} ({a.type_absence.nom})",
            "start": a.date_debut.isoformat(),
            "end": (a.date_fin + timedelta(days=1)).isoformat(),  # FullCalendar exclut le dernier jour
            "type": a.type_absence.nom,
            "collaborateur": a.collaborateur.get_full_name(),
            "color": a.type_absence.couleur,
        })
        
    events = []
    for r in recuperation:
        try:
            date_fin = r.date_debut + timedelta(days=float(r.nombre_jours))
        except Exception:
            date_fin = r.date_debut
        events.append({
            "title": f"{r.collaborateur.get_full_name()} ({r.type_absence.nom})",
            "start": r.date_debut.isoformat(),
            "end": date_fin.isoformat(),  # FullCalendar exclut le dernier jour
            "type": r.type_absence.nom,
            "collaborateur": r.collaborateur.get_full_name(),
            "color": r.type_absence.couleur,
        })
                   

    return render(request, 'collaborateur/calendar_absences.html', {
        'events_json': json.dumps(events),
        'types': types,
        'utilisateurs': utilisateurs,
    })
    
    
# -----------------------------
# Approuver absence
# -----------------------------
@login_required

def approuver_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.approuve_par_superieur = True
    absence.date_approbation_superieur = timezone.now()
    absence.statut = 'approuve_superieur'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        role_valide=request.user.profile.role,
        decision='approuve_par_superieur',
        motif="Approuvé par le supérieur"
    )
    if request.user.profile.role == 'drh':
        return redirect('dashboard_drh')
    else:
        return redirect('dashboard_superieur')


# -----------------------------
# rejet absence
# -----------------------------
@login_required
def rejeter_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)

    if request.method == "POST":
        motif = request.POST.get("motif", "Rejeté par la DRH")
        absence.statut = "rejete"
        absence.save()

        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            decision="rejete",
            motif=motif
        )
        messages.success(request, "L’absence a bien été rejetée.")
        return redirect("dashboard_drh")

    return render(request, "dashboard/drh.html", {"absence": absence})


# -----------------------------
# dashboard pour les DRH
# -----------------------------


@login_required
def dashboard_drh(request):
    if request.user.profile.role != 'drh':
        messages.error(request, "Accès non autorisé.")
        return redirect('login')
    
    annee_courante = date.today().year
    annee_precedente = annee_courante - 1
    absences_annulables_ids = set(
    Absence.objects.filter(statut='valide_dp').values_list('id', flat=True))
    absences_annulables = Absence.objects.filter(statut='valide_dp')

    # -------------------------
    # FILTRES (Suivi & Historique)
    # -------------------------
    mois = request.GET.get('mois')
    type_id = request.GET.get('type')
    statut = request.GET.get('statut')

    filters = {}
    if mois:
        filters['date_debut__month'] = int(mois)
    if type_id:
        filters['type_absence_id'] = int(type_id)
    if statut:
        filters['statut'] = statut

    absences_filtrees = Absence.objects.select_related(
        'collaborateur', 'type_absence'
    ).prefetch_related(
        'historiques'
    ).filter(**filters).order_by('-date_debut')

    # -------------------------
    # DEMANDES RH (à vérifier)
    # -------------------------
    absences_a_verifier = Absence.objects.filter(
        statut='en_attente'
    ).select_related('collaborateur', 'type_absence')

    # -------------------------
    # SUPERVISION + ANNULATIONS DRH
    # -------------------------
    collaborateurs = Profile.objects.filter(
        superieur=request.user
    ).values_list('user', flat=True)

    absences_supervision = Absence.objects.filter(
        collaborateur__in=collaborateurs
    ).select_related('collaborateur', 'type_absence').order_by('-date_debut')

    # -------------------------
    # HISTORIQUE GLOBAL
    # -------------------------
    historiques = ValidationHistorique.objects.select_related(
        'absence', 'utilisateur'
    ).order_by('-date_validation')
    
    # =========================
# REPORT AUTOMATIQUE DES QUOTAS (UNE SEULE FOIS)
# =========================
    users = User.objects.filter(profile__role='collaborateur')
    types = TypeAbsence.objects.all()

    for user in users:
        for t in types:
            quota_ancien = QuotaAbsence.objects.filter(
                user=user,
                type_absence=t,
                annee=annee_precedente
            ).first()

            # Rien à reporter
            if not quota_ancien or quota_ancien.jours_disponibles <= 0:
                continue

            quota_nouveau, created = QuotaAbsence.objects.get_or_create(
                user=user,
                type_absence=t,
                annee=annee_courante,
                defaults={
                    'jours_disponibles': quota_ancien.jours_disponibles
                }
            )

            # Si le quota 2026 existe déjà → on ne reporte pas
            if not created:
                continue


# =========================
# QUOTAS À AFFICHER (ANNÉE COURANTE UNIQUEMENT)
# =========================
    types = list(TypeAbsence.objects.all())
    users = User.objects.filter(profile__role='collaborateur')

    quota_rows = []

    for user in users:
        quotas_ligne = []
        for t in types:
            quota = QuotaAbsence.objects.filter(
                user=user,
                type_absence=t,
                annee=annee_courante
            ).first()
            quotas_ligne.append(quota)

        quota_rows.append({
            'user': user,
            'quotas': quotas_ligne
        })

    # -------------------------
    # RÉCUPÉRATIONS
    # -------------------------
    recuperations = Recuperation.objects.select_related(
        'utilisateur'
    ).order_by('-date_soumission')

    context = {
        'absences_a_verifier': absences_a_verifier,
        'absences_annulables': absences_annulables,
        'absences_annulables_ids': absences_annulables_ids,
        'absences_supervision': absences_supervision,
        'absences': absences_filtrees,
        'historiques': historiques,
        'types': types,
        'quota_rows': quota_rows,
        'recuperations': recuperations,
        'absence_statuts': STATUT_ABSENCE,
        'mois_list': [(i, month_name[i]) for i in range(1, 13)],
        'mois_selectionne': int(mois) if mois else None,
        'type_selectionne': int(type_id) if type_id else None,
        'statut_selectionne': statut,
    }

    return render(request, 'dashboard/drh.html', context)


@login_required
def valider_recuperation(request, recuperation_id):
    recup = get_object_or_404(Recuperation, id=recuperation_id)
    if recup.statut == 'en_attente':
        recup.statut = 'verifie_drh'
        recup.save()
        messages.success(request, f"La récupération de {recup.utilisateur.get_full_name()} a été vérifiée par la DRH et transmise au DP.")
    else:
        messages.error(request, "Impossible de vérifier cette récupération (statut incorrect).")
    return redirect('dashboard_drh')


@login_required
def modifier_absence_drh(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    if request.method == "POST":
        commentaire = request.POST.get("commentaire")
        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            decision="amélioration",
            motif=commentaire,
        )
        messages.info(request, "Suggestion d’amélioration envoyée.")
    return redirect("dashboard_drh")


# -----------------------------
# verifier et rejeter les absences par la DRH
# -----------------------------
@login_required
def verifier_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.verifie_par_drh = True
    absence.date_verification_drh = timezone.now()
    absence.statut = 'verifie_drh'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        decision='verifie_par_drh',
        motif="Vérifié par la DRH"
    )
    return redirect('dashboard_drh')

@login_required
def rejeter_absence_drh(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    if request.method == "POST":
        commentaire = request.POST.get("commentaire", "Rejeté par la DRH")
        absence.statut = "rejete"
        absence.save()
        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            decision="rejet",
            motif=commentaire,
        )
        messages.warning(request, "Absence rejetée.")
    return redirect("dashboard_drh")

# -----------------------------
# Mettre a jour quota absence
# -----------------------------
@login_required
def mettre_a_jour_quota(request, quota_id):
    quota = get_object_or_404(QuotaAbsence, id=quota_id)

    if request.method == 'POST':
        print("=== POST reçu ===", request.POST)

        jours_str = request.POST.get('jours', '').strip()
        operation = request.POST.get('operation')

        # Validation basique
        if not jours_str:
            messages.error(request, "Veuillez entrer un nombre de jours.")
            return redirect('dashboard_drh')

        try:
            jours = Decimal(jours_str)
        except InvalidOperation:
            messages.error(request, "Veuillez entrer un nombre de jours valide (ex: 1.5).")
            return redirect('dashboard_drh')

        if jours <= 0:
            messages.error(request, "Le nombre de jours doit être supérieur à zéro.")
            return redirect('dashboard_drh')

        # 🔑 Sécurité : si jamais des anciennes lignes sont NULL
        if quota.jours_disponibles is None:
            quota.jours_disponibles = Decimal("0.00")

        # Application de l’opération
        if operation == 'ajouter':
            quota.jours_disponibles += jours
            messages.success(request, f"{jours} jour(s) ajouté(s) avec succès.")
        elif operation == 'reduire':
            if jours > quota.jours_disponibles:
                messages.error(request, "Impossible de réduire au-delà du quota disponible.")
                return redirect('dashboard_drh')
            quota.jours_disponibles -= jours
            messages.success(request, f"{jours} jour(s) réduit(s) avec succès.")
        else:
            messages.error(request, "Opération non reconnue.")
            return redirect('dashboard_drh')

        quota.save()

    return redirect('dashboard_drh')

def telecharger_justificatif(request, file_path):
    blob_service_client = BlobServiceClient(
        f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net",
        credential=settings.AZURE_ACCOUNT_KEY
    )
    container_client = blob_service_client.get_container_client(settings.AZURE_CONTAINER)
    blob_client = container_client.get_blob_client(file_path)

    stream = blob_client.download_blob()
    response = HttpResponse(
        stream.readall(),
        content_type='application/octet-stream'
    )
    response['Content-Disposition'] = f'attachment; filename="{file_path}"'
    return response

# -----------------------------
# Dashboard pour le Directeur Pays
# -----------------------------


@login_required
def dashboard_dp(request):
    profil = Profile.objects.get(user=request.user)
    historiques = ValidationHistorique.objects.all().order_by('-date_validation')
    recuperations = Recuperation.objects.all().order_by('-date_soumission')

    # Fusionner les deux listes
    donnees = list(historiques) + list(recuperations)

    # Trier par date (la plus récente)
    donnees.sort(
        key=lambda x: x.date_validation if hasattr(x, 'date_validation') else x.date_soumission,
        reverse=True
    )

    paginator = Paginator(donnees, 10)  
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    
    # Collaborateurs sous la responsabilité du DP (où il est supérieur hiérarchique)
    collaborateurs_sous_dp = Profile.objects.filter(superieur=request.user).values_list('user', flat=True)

    # Absences à valider :
    # - approuvées par les supérieurs (DP doit valider)
    # - vérifiées par la RH mais dont le DP est aussi le supérieur hiérarchique direct
    absences_a_valider_dp = Absence.objects.filter(
        Q(statut='approuve_superieur') |
        Q(statut='verifie_drh', collaborateur__in=collaborateurs_sous_dp)
    ).select_related('collaborateur', 'type_absence').order_by('date_debut')

    # Absences planifiées visibles par le DP
    mois_selectionne = int(request.GET.get('mois', datetime.now().month))
    type_id = request.GET.get('type')

    absences_planifiees = Absence.objects.filter(
        Q(statut__in=['en_attente', 'approuve_superieur', 'verifie_drh', 'valide_dp']),
        date_debut__month=mois_selectionne
    ).select_related('collaborateur', 'type_absence')

    if type_id:
        absences_planifiees = absences_planifiees.filter(type_absence_id=type_id)

    absences_planifiees = absences_planifiees.order_by('date_debut')

    absences_validees = Absence.objects.filter(
        statut='valide_dp'
    ).order_by('date_debut')
    
    recuperations_validees = Recuperation.objects.filter(
        statut='valide'
    )

    # --- Gestion des récupérations --- #
    recuperation = Recuperation.objects.filter(
        statut='verifie_drh'
    ).select_related('utilisateur').order_by('-date_soumission')

    for recup in recuperation:
        recup.date_fin = recup.date_debut + timedelta(days=float(recup.nombre_jours) - 1)
    for recupfin in recuperations_validees:
        recupfin.date_fin = recupfin.date_debut + timedelta(days=float(recupfin.nombre_jours) - 1)

    types = TypeAbsence.objects.all()
    mois_list = [(i, month_name[i]) for i in range(1, 13)]

    context = {
        'absences_planifiees': absences_planifiees,
        'absences_a_valider_dp': absences_a_valider_dp,
        'absences_validees': absences_validees,
        'recuperations_validees': recuperations_validees,
        'recuperation': recuperation,
        'mois_list': mois_list,
        'mois_selectionne': mois_selectionne,
        'types': types,
        'type_selectionne': int(type_id) if type_id else None,
        'historiques': historiques,
        'recuperations': recuperations,
        'page_obj': page_obj,
    }
    return render(request, 'dashboard/dp.html', context)


# -----------------------------
# Absences DP
# -----------------------------

@login_required
def valider_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    profil_dp = Profile.objects.get(user=request.user)

    # Cas 1 : absence approuvée par un supérieur (normal)
    # Cas 2 : absence vérifiée RH mais DP est aussi supérieur du collaborateur
    if absence.statut == 'approuve_superieur' or (
        absence.statut == 'verifie_drh' and absence.collaborateur.profile.superieur == request.user
    ):
        absence.statut = 'valide_dp'
        absence.date_validation_dp = timezone.now()
        absence.save()

        ValidationHistorique.objects.create(
            absence=absence,
            utilisateur=request.user,
            role_valide='dp',
            decision='valider'
        )
        messages.success(request, f"L'absence de {absence.collaborateur.get_full_name()} a été validée par le DP.")
    else:
        messages.error(request, "Cette absence ne peut pas être validée (statut incorrect ou non autorisée).")

    return redirect('dashboard_dp')

@login_required
def rejeter_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    profil_dp = Profile.objects.get(user=request.user)

    if request.method == 'POST':
        motif = request.POST.get('motif', '').strip()

        if absence.statut in ['approuve_superieur', 'verifie_drh']:
            # Le DP peut rejeter dans les mêmes conditions que valider
            if absence.statut == 'verifie_drh' and absence.collaborateur.profile.superieur != request.user:
                messages.error(request, "Vous ne pouvez pas rejeter cette absence (non sous votre supervision).")
                return redirect('dashboard_dp')

            absence.statut = 'rejete'
            absence.save()

            ValidationHistorique.objects.create(
                absence=absence,
                utilisateur=request.user,
                role_valide='dp',
                decision='rejeter',
                motif=motif
            )
            messages.success(request, f"L'absence de {absence.collaborateur.get_full_name()} a été rejetée.")
        else:
            messages.error(request, "Impossible de rejeter cette absence (statut incorrect).")

    return redirect('dashboard_dp')


@login_required
def annuler_absence_drh(request, absence_id):
    # Sécurité DRH
    if request.user.profile.role != 'drh':
        messages.error(request, "Action non autorisée.")
        return redirect('dashboard_drh')

    absence = get_object_or_404(Absence, id=absence_id)

    if request.method != 'POST':
        return redirect('dashboard_drh')

    motif = request.POST.get('motif', '').strip()
    if not motif:
        messages.error(request, "Motif obligatoire.")
        return redirect('dashboard_drh')

    # =========================
    # 1️⃣ HISTORIQUE (AVANT DELETE)
    # =========================
    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        role_valide='drh',
        decision='annulation_drh',
        motif=motif
    )

    # =========================
    # 2️⃣ RÉTABLIR LE QUOTA SI VALIDÉ DP
    # =========================
    if absence.statut == 'valide_dp':
        try:
            quota = QuotaAbsence.objects.get(
                user=absence.collaborateur,
                type_absence=absence.type_absence,
                annee=absence.date_debut.year
            )
            quota.jours_disponibles += absence.nombre_jours
            quota.save()
        except QuotaAbsence.DoesNotExist:
            pass  # sécurité

    # =========================
    # 3️⃣ HARD DELETE
    # =========================
    absence.delete()

    messages.success(
        request,
        "Absence annulée définitivement par la DRH. Les dates sont à nouveau disponibles."
    )

    return redirect('dashboard_drh')


# -----------------------------
# Récupérations DP
# -----------------------------

@login_required
def valider_recuperation_dp(request, recup_id):
    recup = get_object_or_404(Recuperation, id=recup_id)

    # Le DP valide uniquement une récupération déjà vérifiée par la DRH
    if recup.statut == 'verifie_drh':
        recup.statut = 'valide'
        recup.save()
        messages.success(request, f"La récupération de {recup.utilisateur.get_full_name()} a été validée par le DP.")
    else:
        messages.error(request, "Impossible de valider cette récupération (statut incorrect).")
    return redirect('dashboard_dp')


@login_required
def rejeter_recuperation_dp(request, recup_id):
    recup = get_object_or_404(Recuperation, id=recup_id)

    if request.method == 'POST':
        motif = request.POST.get('motif', '').strip()
        if recup.statut == 'verifie_drh':
            recup.statut = 'rejete'
            # tu utilises ici motif_annulation pour stocker le motif du rejet
            recup.motif_annulation = motif
            recup.save()
            messages.success(request, f"La récupération de {recup.utilisateur.get_full_name()} a été rejetée par le DP.")
        else:
            messages.error(request, "Impossible de rejeter cette récupération (statut incorrect).")
    return redirect('dashboard_dp')


@login_required
def annuler_recuperation_dp(request, recup_id):
    recup = get_object_or_404(Recuperation, id=recup_id)

    if recup.statut == 'valide':
        recup.statut = 'en_attente'
        recup.save()

        ValidationHistorique.objects.create(
            absence=None,
            utilisateur=request.user,
            role_valide='dp',
            decision='annuler',
            motif='Annulation par DP'
        )
        messages.success(request, f"La récupération de {recup.utilisateur.get_full_name()} a été annulée.")
    else:
        messages.error(request, "Impossible d'annuler cette récupération (non validée).")
    return redirect('dashboard_dp')


@login_required
def exporter_absences_excel(request):
    
    mois = int(request.GET.get('mois', datetime.now().month))
    type_id = request.GET.get('type')

    absences = Absence.objects.filter(
    statut='verifie_drh',
    date_debut__month=mois
)
    if type_id:
        absences = absences.filter(type_absence_id=type_id)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="absences.csv"'

    writer = csv.writer(response)
    writer.writerow(['Nom', 'Type', 'Début', 'Fin', 'Statut', 'Raison'])

    for a in absences:
        writer.writerow([
            a.collaborateur.get_full_name(),
            a.type_absence.nom,
            a.date_debut,
            a.date_fin,
            a.get_statut_display(),
            a.raison or ''
        ])

    return response



@login_required
def admin_users(request):
    utilisateurs = User.objects.select_related('profile').all().order_by('last_name')
    types_absences = TypeAbsence.objects.all()
    annees = Annee.objects.order_by('-annee')
    superieurs = User.objects.exclude(profile__role='collaborateur')
    

    if request.method == 'POST':
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')

        # Création ou mise à jour
        if action in ['create', 'edit']:
            nom = request.POST['nom']
            prenom = request.POST['prenom']
            email = request.POST['email']
            username = request.POST['username']
            poste = request.POST['poste'] 
            role = request.POST['role']
            superieur_id = request.POST.get('superieur')
            annee_id = request.POST.get('annee')
            quotas = request.POST.getlist('quota')

            if action == 'create':
                user = User.objects.create(
                    username=username,
                    first_name=prenom,
                    last_name=nom,
                    email=email,
                    password = make_password('1234')
                )
                Profile.objects.create(
                    user=user,
                    role=role,
                    superieur=User.objects.get(id=superieur_id) if superieur_id else None,
                    actif=True, 
                    doit_changer_mdp=True, 
                    poste = poste
                )
                messages.success(request, "Utilisateur créé avec succès.")
            else:
                user = get_object_or_404(User, id=user_id)
                user.username = username
                user.first_name = prenom
                user.last_name = nom
                user.email = email
                user.save()

                profile = user.profile
                profile.role = role
                profile.poste = poste
                profile.superieur = User.objects.get(id=superieur_id) if superieur_id else None
                profile.actif = 'actif' in request.POST
                profile.save()
                messages.success(request, "Utilisateur modifié avec succès.")

            for i, type_absence in enumerate(types_absences):
                # conversion sécurisée
                try:
                    jours = float(quotas[i].replace(',', '.'))
                except (ValueError, IndexError):
                    jours = 0.0  
                quota, created = QuotaAbsence.objects.get_or_create(
                    user=user,
                    type_absence=type_absence,
                    annee=annee_id,
                    defaults={'jours_disponibles': jours}
                )
                if not created:
                    quota.jours_disponibles = jours
                    quota.save()

        elif action == 'delete':
            user = get_object_or_404(User, id=user_id)
            user.delete()
            messages.success(request, "Utilisateur supprimé.")

        return redirect('admin_users')

    return render(request, 'admin/utilisateurs.html', {
        'utilisateurs': utilisateurs,
        'types': types_absences,
        'annees': annees,
        'superieurs': superieurs,
        'roles': ROLES,
        'absences': Absence.objects.select_related('collaborateur', 'type_absence').all(),
    })


def configuration_view(request):
    # --- Pré-remplissage des mois s'ils n'existent pas déjà
    mois_noms = [
        "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
    ]
    if Mois.objects.count() != 12:
        for i in range(1, 13):
            Mois.objects.get_or_create(nom=mois_noms[i-1], numero=i)

    # --- Gestion des ajouts
    if request.method == "POST":
        if 'ajouter_jourferie' in request.POST:
            date_jf = request.POST.get('date')
            description = request.POST.get('description')
            if not JourFerie.objects.filter(date=date_jf).exists():
                JourFerie.objects.create(date=date_jf, description=description)
                messages.success(request, "Jour férié ajouté.")
            else:
                messages.warning(request, "Ce jour férié existe déjà.")

        elif 'ajouter_annee' in request.POST:
            annee = request.POST.get('annee')
            if not Annee.objects.filter(annee=annee).exists():
                Annee.objects.create(annee=annee)
                messages.success(request, "Année ajoutée.")
            else:
                messages.warning(request, "Cette année existe déjà.")

        elif 'ajouter_typeabsence' in request.POST:
            nom = request.POST.get('nom')
            couleur = request.POST.get('couleur')
            if not TypeAbsence.objects.filter(nom=nom).exists():
                TypeAbsence.objects.create(nom=nom, couleur=couleur)
                messages.success(request, "Type d'absence ajouté.")
            else:
                messages.warning(request, "Ce type d'absence existe déjà.")
                
        elif 'modifier_typeabsence' in request.POST:
            type_id = request.POST.get('modifier_typeabsence_id')
            nom = request.POST.get('nom')
            couleur = request.POST.get('couleur')

            try:
                type_abs = TypeAbsence.objects.get(id=type_id)
                type_abs.nom = nom
                type_abs.couleur = couleur
                type_abs.save()
                messages.success(request, "Type d'absence modifié.")
            except TypeAbsence.DoesNotExist:
                messages.error(request, "Type d'absence introuvable.")
                
        elif 'modifier_jourferie' in request.POST:
            jourferie_id = request.POST.get('modifier_jourferie_id')
            nouvelle_date = request.POST.get('date')
            nouvelle_description = request.POST.get('description')

            try:
                jf = JourFerie.objects.get(id=jourferie_id)
                jf.date = nouvelle_date
                jf.description = nouvelle_description
                jf.save()
                messages.success(request, "Jour férié modifié.")
            except JourFerie.DoesNotExist:
                messages.error(request, "Jour férié introuvable.")



        return redirect('configuration_view')  # Redirection après post

    # --- Contexte pour affichage
    context = {
        'jours_feries': JourFerie.objects.all().order_by('date'),
        'annees': Annee.objects.all().order_by('-annee'),
        'mois': Mois.objects.all().order_by('numero'),
        'types_absence': TypeAbsence.objects.all().order_by('nom'),
    }
    return render(request, 'admin/configurations.html', context)


@login_required
def supprimer_type_absence(request, type_id):
    type_abs = get_object_or_404(TypeAbsence, id=type_id)
    type_abs.delete()
    messages.success(request, "Type d'absence supprimé.")
    return redirect('configuration_view')

@login_required
def supprimer_jour_ferie(request, jour_id):
    jf = get_object_or_404(JourFerie, id=jour_id)
    jf.delete()
    messages.success(request, "Jour férié supprimé.")
    return redirect('configuration_view')

@login_required
def soumettre_recuperation(request):
    if request.method == 'POST':
        motif = request.POST.get('motif')
        date_debut = request.POST.get('date_debut')
        nombre_jours = request.POST.get('nombre_jours')
        justificatif = request.FILES.get('justificatif')

        if not (motif and date_debut and nombre_jours and justificatif):
            messages.error(request, "Tous les champs sont requis.")
            return redirect('dashboard_collaborateur')  # ou page actuelle

        try:
            date_debut_obj = datetime.strptime(date_debut, "%Y-%m-%d").date()
            nombre_jours_float = float(nombre_jours)
        except ValueError:
            messages.error(request, "Format de date ou nombre de jours invalide.")
            return redirect('dashboard_collaborateur')

        # Créer la récupération
        Recuperation.objects.create(
            utilisateur=request.user,  # <=== au lieu de collaborateur
            motif=motif,
            date_debut=date_debut_obj,
            nombre_jours=nombre_jours_float,
            justificatif=justificatif
        )

        messages.success(request, "Récupération soumise avec succès !")
        return redirect('mes_absences')

    return redirect('dashboard_collaborateur')

# -----------------------------
# Modifier une récupération
# -----------------------------
@login_required
def modifier_recuperation(request, recup_id):
    recup = get_object_or_404(Recuperation, id=recup_id, utilisateur=request.user)

    if recup.statut == 'valide':
        messages.error(request, "Cette récupération est déjà validée et ne peut pas être modifiée.")
        return redirect('mes_absences')

    if request.method == 'POST':
        date_debut = request.POST.get('date_debut')
        nombre_jours = request.POST.get('nombre_jours')
        motif = request.POST.get('motif')
        justificatif = request.FILES.get('justificatif')
        if nombre_jours:
            recup.nombre_jours = nombre_jours
        if motif:
            recup.motif = motif
        if justificatif:
            recup.justificatif = justificatif

        # Calcul de date_fin pour affichage (similaire aux absences)
        if date_debut:
            recup.date_debut = datetime.strptime(date_debut, "%Y-%m-%d").date()
        if nombre_jours:
            recup.nombre_jours = float(nombre_jours)
        # Calcul date_fin
        recup.date_fin = recup.date_debut + timedelta(days=recup.nombre_jours - 1)

        recup.save()
        messages.success(request, "Récupération modifiée avec succès.")
        return redirect('mes_absences')

    return render(request, 'collaborateur/modifier_recuperation.html', {'recup': recup})


# -----------------------------
# Annuler une récupération
# -----------------------------
@login_required
def annuler_recuperation(request, recup_id):
    recup = get_object_or_404(Recuperation, id=recup_id, utilisateur=request.user)

    # Empêche l’annulation si déjà validée
    if recup.statut == 'valide':
        messages.error(request, "Cette récupération est déjà validée et ne peut pas être annulée.")
        return redirect('mes_absences')

    if request.method == 'POST':
        motif = request.POST.get('motif_annulation')
        if not motif:
            messages.error(request, "Veuillez fournir un motif d'annulation.")
            return redirect('mes_absences')

        recup.statut = 'annulee'
        recup.motif_annulation = motif
        recup.save()

        messages.success(request, "Votre récupération a été annulée avec succès.")
        return redirect('mes_absences')

    return render(request, 'collaborateur/annuler_recuperation.html', {'recup': recup})
