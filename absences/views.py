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
import json
from django.contrib.auth.hashers import make_password
from django.utils.html import escape
from datetime import date
from django.db.models import Q
from django.db.models.functions import TruncMonth
from django.db.models import Count
from django.contrib.auth.decorators import login_required, user_passes_test
from calendar import month_name
from django.db.models.functions import ExtractMonth
from django.http import HttpResponse
import csv



# -----------------------------
# Accueil 
# -----------------------------
def accueil_public(request):
    # Génère les noms des mois en français
    mois_noms = [month_name[i].capitalize() for i in range(1, 13)]

    # Récupère tous les utilisateurs actifs avec des absences validées
    utilisateurs = User.objects.filter(profile__actif=True).order_by('last_name')

    lignes = []
    for user in utilisateurs:
        # Récupère toutes les absences validées pour cet utilisateur
        absences = Absence.objects.filter(
            collaborateur=user,
            statut='valide_dp'
        ).order_by('date_debut')

        # Initialise les absences par mois
        absences_par_mois = [[] for _ in range(12)]
        total_absences = 0

        for absence in absences:
            mois = absence.date_debut.month - 1
            absences_par_mois[mois].append(absence)
            total_absences += absence.duree()

        lignes.append({
            'user': user,
            'mois': absences_par_mois,
            'total': total_absences
        })

    return render(request, 'accueil.html', {
        'mois_noms': mois_noms,
        'lignes': lignes
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
    return redirect('login')

# -----------------------------
# Dashboard pour les supérieurs
# -----------------------------

@login_required
def dashboard_superieur(request):
    profil = Profile.objects.get(user=request.user)
    collaborateurs = Profile.objects.filter(superieur=request.user, role='collaborateur').values_list('user', flat=True)

    absences_a_valider = Absence.objects.filter(
        collaborateur__in=collaborateurs,
        statut='en_attente'
    )

    context = {
        'absences': absences_a_valider
    }
    return render(request, 'dashboard/superieurs.html', context)

# -----------------------------
# Dashboard pour les collaborateurs
# -----------------------------
@login_required
def dashboard_collaborateur(request):
    return render(request, 'dashboard/collaborateurs.html')


# -----------------------------
# Soumettre une absence
# ----------------------------- 
@login_required
def soumettre_absence(request):
    types_absence = TypeAbsence.objects.all()

    if request.method == 'POST':
        # Récupération des données du formulaire
        type_id = request.POST.get('type_absence')
        date_debut = request.POST.get('date_debut')
        nombre_jours = request.POST.get('nombre_jours')
        raison = request.POST.get('raison')
        justificatif = request.FILES.get('justificatif')

        if not type_id or not date_debut or not nombre_jours:
            messages.error(request, "Tous les champs obligatoires doivent être remplis.")
            return redirect(request.path)

        try:
            type_absence = TypeAbsence.objects.get(pk=type_id)
            date_debut_obj = datetime.strptime(date_debut, "%Y-%m-%d").date()
            nombre_jours_int = int(nombre_jours)
            if nombre_jours_int <= 0:
                raise ValueError
        except (TypeAbsence.DoesNotExist, ValueError):
            messages.error(request, "Données invalides dans le formulaire.")
            return redirect(request.path)

        # Création de l'objet absence
        absence = Absence(
            collaborateur=request.user,
            type_absence=type_absence,
            date_debut=date_debut_obj,
            nombre_jours=nombre_jours_int,
            raison=raison,
            justificatif=justificatif
        )

        try:
            absence.full_clean()
            absence.save()
            messages.success(request, "Demande d’absence soumise avec succès.")
            return redirect('mes_absences')
        except Exception as e:
            messages.error(request, f"Une erreur est survenue : {e}")
            return redirect(request.path)

    # Partie GET (affichage du formulaire)
    jours_feries_qs = JourFerie.objects.all()
    jours_feries = [j.date.strftime('%Y-%m-%d') for j in jours_feries_qs]

    return render(request, 'collaborateur/soumettre_absence.html', {
        'types_absence': types_absence,
        'jours_feries': jours_feries,
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
@login_required
def mes_absences(request):
    absences = Absence.objects.filter(collaborateur=request.user).order_by('-date_creation')
    return render(request, 'collaborateur/mes_absences.html', {'absences': absences})


# -----------------------------
# calendrier des absences
# -----------------------------
@login_required
def calendrier_absences(request):
    absences = Absence.objects.filter(statut='valide_dp')
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
        action='approuve_par_superieur',
        commentaire="Approuvé par le supérieur"
    )
    return redirect('dashboard_superieur')


# -----------------------------
# rejet absence
# -----------------------------
@login_required
def rejeter_absence(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.statut = 'rejete'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='rejete',
        commentaire="Rejeté par le supérieur"
    )
    return redirect('dashboard_superieur')


# -----------------------------
# dashboard pour les DRH
# -----------------------------
@login_required
def dashboard_drh(request):
    absences = Absence.objects.all()
    mois = request.GET.get('mois')
    type_id = request.GET.get('type')

    # Filtre dynamique
    absences_filtrees = Absence.objects.all()
    if mois:
        absences_filtrees = absences_filtrees.filter(date_debut__month=mois)
    if type_id:
        absences_filtrees = absences_filtrees.filter(type_absence_id=type_id)

    # Groupes
    absences_a_verifier = absences_filtrees.filter(statut='approuve_superieur')
    absences_validees_dp = absences_filtrees.filter(statut='valide_dp')
    absences_a_approuver = absences_filtrees.filter(statut='verifie_rh')

    types_absence = TypeAbsence.objects.all()
    stats_par_type = absences_filtrees.values('type_absence__nom').annotate(total=Count('id'))
    
    mois = request.GET.get('mois')
    type_id = request.GET.get('type')
    statut = request.GET.get('statut')

    absences = Absence.objects.select_related('collaborateur', 'collaborateur__profile', 'type_absence')

    if mois:
        absences = absences.filter(date_debut__month=int(mois))
    if type_id:
        absences = absences.filter(type_absence_id=type_id)
    if statut:
        absences = absences.filter(statut=statut)

    types = TypeAbsence.objects.all()
    statuts = STATUT_ABSENCE
    
    # ...
    current_year = datetime.now().year
    users = User.objects.filter(profile__actif=True).order_by('last_name')
    absences = Absence.objects.filter(
        statut='valide_dp',
        date_debut__year=current_year
    )

    lignes = []
    for user in users:
        ligne = {'user': user, 'mois': [], 'total': 0}
        for mois in range(1, 13):
            absences_mois = []
            for a in absences:
                if a.collaborateur == user and a.date_debut.month <= mois <= a.date_fin.month:
                    absences_mois.append(a)
            ligne['mois'].append(absences_mois)
            ligne['total'] += sum(a.nombre_jours for a in absences_mois)
        lignes.append(ligne)

    mois_noms = [month_name[i][:4] for i in range(1, 13)]

    context = {
        'lignes': lignes,
        'mois_noms': mois_noms,
        'absences': absences,
        'types': types,
        'statuts': statuts,
        'mois_selectionne': mois,
        'type_selectionne': type_id,
        'statut_selectionne': statut,
        'absences_a_verifier': absences_a_verifier,
        'absences_validees_dp': absences_validees_dp,
        'absences_a_approuver': absences_a_approuver,
        'types_absence': types_absence,
        'stats_par_type': stats_par_type,
        'mois_selectionne': mois,
        'type_selectionne': type_id,
    }
    return render(request, 'dashboard/drh.html', context)
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
        action='verifie_par_drh',
        commentaire="Vérifié par la DRH"
    )
    return redirect('dashboard_drh')

@login_required
def rejeter_absence_drh(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.statut = 'rejete'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='rejete',
        commentaire="Rejeté par la DRH"
    )
    return redirect('dashboard_drh')


# -----------------------------
# Dashboard pour le Directeur Pays
# -----------------------------


@login_required
def dashboard_dp(request):
    profil = Profile.objects.get(user=request.user)
    collaborateurs = Profile.objects.filter(superieur=request.user, role='drh').values_list('user', flat=True)

    absences_a_valider = Absence.objects.filter(
        collaborateur__in=collaborateurs,
        statut='en_attente'
    )
    
    mois_selectionne = int(request.GET.get('mois', datetime.now().month))
    type_id = request.GET.get('type')

    absences_planifiees = Absence.objects.filter(
        Q(statut__in=['en_attente', 'approuve_superieur', 'verifie_drh', 'valide_dp']),
        date_debut__month=mois_selectionne
    )
    if type_id:
        absences_planifiees = absences_planifiees.filter(type_absence_id=type_id)
    absences_planifiees = absences_planifiees.order_by('date_debut')

    absences_a_valider_dp = Absence.objects.filter(
        statut='verifie_drh'
    ).order_by('date_debut')

    absences_validees = Absence.objects.filter(statut='valide_dp').order_by('date_debut')

    types = TypeAbsence.objects.all()
    mois_list = [(i, month_name[i]) for i in range(1, 13)]

    context = {
        'absences_planifiees': absences_planifiees,
        'absences_a_valider_dp': absences_a_valider_dp,
        'absences_validees': absences_validees,
        'mois_list': mois_list,
        'mois_selectionne': mois_selectionne,
        'types': types,
        'type_selectionne': int(type_id) if type_id else None,
        'absences' : absences_a_valider,
    }
    return render(request, 'dashboard/dp.html', context)



@login_required
def valider_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.statut = 'valide'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='valide_par_dp',
        commentaire="Validé par le Directeur Pays"
    )
    return redirect('dashboard_dp')


@login_required
def rejeter_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.statut = 'rejete'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='rejete_par_dp',
        commentaire="Rejeté par le Directeur Pays"
    )
    return redirect('dashboard_dp')


@login_required
def exporter_absences_excel(request):
    
    mois = int(request.GET.get('mois', datetime.now().month))
    type_id = request.GET.get('type')

    absences = Absence.objects.filter(
        statut='verifie_rh',
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
def valider_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)

    absence.valide_par_dp = True
    absence.date_validation_dp = timezone.now()
    absence.statut = 'valide_dp'
    absence.save()  # déclenche la déduction de quota + historique dans model.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='valide_par_dp',
        commentaire="Validé définitivement par DP"
    )
    return redirect('dashboard_dp')



@login_required
def rejeter_absence_dp(request, absence_id):
    absence = get_object_or_404(Absence, id=absence_id)
    absence.statut = 'rejete'
    absence.save()

    ValidationHistorique.objects.create(
        absence=absence,
        utilisateur=request.user,
        action='rejete_par_dp',
        commentaire="Rejeté par le DP"
    )
    return redirect('dashboard_dp')



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
                jours = quotas[i]
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

    return render(request, 'admin/dashboard.html', {
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

        return redirect('configuration_view')  # Redirection après post

    # --- Contexte pour affichage
    context = {
        'jours_feries': JourFerie.objects.all().order_by('date'),
        'annees': Annee.objects.all().order_by('-annee'),
        'mois': Mois.objects.all().order_by('numero'),
        'types_absence': TypeAbsence.objects.all().order_by('nom'),
    }
    return render(request, 'admin/configurations.html', context)